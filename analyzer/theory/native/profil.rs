//! Profil d'adversaire : la stratégie du solveur d'un joueur, inclinée situation par situation vers
//! ce qu'il fait réellement, puis verrouillée pour lire la meilleure réponse (l'exploit) en face.
//!
//! Analyzer donne, pour chaque situation (c-bet, 2e barrel, fold face à une mise...), un rapport de
//! cotes : fréquence observée contre fréquence théorique, ramené vers 1 quand l'échantillon est
//! petit. À chaque nœud de l'adversaire, la fréquence agrégée du solveur (pondérée par la portée)
//! est déplacée de ce rapport de cotes : un joueur qui c-bet plus que la théorie mise plus partout,
//! mais toujours davantage là où le solveur mise déjà beaucoup. Pour y arriver, toutes les mains
//! du nœud reçoivent le même multiplicateur sur les cotes de l'action : l'ordre des mains et la
//! répartition entre les tailles sont gardés, et les mains jouées pures le restent.
//!
//! Les situations absentes du profil gardent la stratégie du solveur.

use serde::Deserialize;
use serde_json::{json, Map, Value};
use solver::cards::permute_card;
use solver::game::Dealt;
use solver::tree::{Action, KIND_ACTION, KIND_CHANCE, SENTINEL};
use solver::Solver;
use std::collections::BTreeMap;

#[derive(Deserialize, Clone, PartialEq, Debug)]
pub struct Profile {
    /// Joueur verrouillé : 0 = hors de position, 1 = en position.
    pub villain: usize,
    /// Agresseur préflop, qui a l'initiative au flop.
    pub aggressor: usize,
    /// Rapport de cotes par situation (voir `situations`).
    pub tilts: BTreeMap<String, f64>,
}

const LABEL: &str = "profil:";

#[derive(Clone, Copy)]
struct History {
    /// Dernier joueur à avoir misé ou relancé (l'agresseur préflop au départ).
    aggressor: usize,
    /// Mises et relances de la street en cours.
    level: u8,
    /// L'adversaire a misé ou relancé à cette street, à la précédente.
    bet_now: bool,
    bet_before: bool,
}

impl History {
    fn after(self, actor: usize, action: &Action, villain: usize) -> Self {
        let mut next = self;
        if action.is_aggressive() {
            next.aggressor = actor;
            next.level += 1;
            next.bet_now |= actor == villain;
        }
        next
    }

    fn next_street(self) -> Self {
        History { level: 0, bet_now: false, bet_before: self.bet_now, ..self }
    }
}

/// Situations de l'adversaire à un nœud : (fold, action agressive). Face à une mise, le fold puis
/// la relance ; sinon, la mise.
fn situations(p: &Profile, h: &History, street: u8, facing: bool) -> (Option<&'static str>, Option<&'static str>) {
    let v = p.villain;
    if facing {
        if h.level <= 1 {
            (Some(["fold_flop", "fold_turn", "fold_river"][street.min(2) as usize]), Some("raise"))
        } else {
            (Some("fold_raise"), Some("reraise"))
        }
    } else if v == p.aggressor && h.aggressor == v {
        let s = match street {
            0 => Some("cbet"),
            1 => Some(if h.bet_before { "barrel" } else { "delayed" }),
            _ if h.bet_before => Some("barrel3"),
            _ => None,
        };
        (None, s)
    } else if v != p.aggressor && h.aggressor != v {
        (None, Some("stab"))
    } else {
        (None, None)
    }
}

#[derive(Default)]
struct Tally {
    mass: f64,
    before: f64,
    after: f64,
    nodes: usize,
}

/// Multiplie, main par main, les cotes des actions `group` parmi les actions `within` par un même
/// facteur, choisi pour que leur part agrégée (pondérée par la portée) passe de la valeur du
/// solveur à celle que donne `ratio`. Renvoie (part avant, part après, masse).
fn tilt(sigma: &mut [f32], na: usize, nh: usize, reach: &[f32], group: &[bool], within: &[bool], ratio: f64) -> (f64, f64, f64) {
    let mut weight = vec![0f64; nh];
    let mut share = vec![0f64; nh];
    let (mut mass, mut total) = (0f64, 0f64);
    for i in 0..nh {
        let (mut w, mut g) = (0f64, 0f64);
        for a in 0..na {
            if within[a] {
                let x = sigma[a * nh + i] as f64;
                w += x;
                if group[a] {
                    g += x;
                }
            }
        }
        if w > 1e-12 {
            share[i] = (g / w).clamp(0.0, 1.0);
            weight[i] = w * reach[i] as f64;
        }
        mass += weight[i];
        total += weight[i] * share[i];
    }
    if mass <= 1e-12 {
        return (0.0, 0.0, 0.0);
    }
    let before = total / mass;
    if before <= 1e-6 || before >= 1.0 - 1e-6 || (ratio - 1.0).abs() < 1e-6 {
        return (before, before, mass);
    }
    let odds = before / (1.0 - before) * ratio;
    let target = odds / (1.0 + odds);
    let aggregate = |m: f64| -> f64 {
        let mut t = 0f64;
        for i in 0..nh {
            let s = share[i];
            if weight[i] > 0.0 && s > 0.0 {
                t += weight[i] * m * s / (1.0 - s + m * s);
            }
        }
        t / mass
    };
    // la part agrégée croît avec le multiplicateur : dichotomie sur son logarithme
    let (mut lo, mut hi) = (-12f64, 12f64);
    for _ in 0..32 {
        let mid = 0.5 * (lo + hi);
        if aggregate(mid.exp()) < target {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    let m = (0.5 * (lo + hi)).exp();
    for i in 0..nh {
        let s = share[i];
        if s <= 0.0 {
            continue;
        }
        let d = 1.0 - s + m * s;
        for a in 0..na {
            if within[a] {
                let f = if group[a] { m / d } else { 1.0 / d };
                sigma[a * nh + i] = (sigma[a * nh + i] as f64 * f) as f32;
            }
        }
    }
    (before, aggregate(m), mass)
}

struct Walker<'a> {
    solver: &'a Solver,
    profile: &'a Profile,
    eps: f64,
    locks: Vec<(u32, Vec<f32>, String)>,
    tally: BTreeMap<&'static str, Tally>,
}

impl Walker<'_> {
    fn walk(&mut self, idx: u32, reach: &[f32], dealt: Dealt, h: History) {
        let mass: f64 = reach.iter().map(|&x| x as f64).sum();
        if mass < self.eps {
            return; // l'adversaire n'arrive pratiquement jamais ici
        }
        let solver = self.solver;
        let spot = &*solver.spot;
        let tree = &spot.tree;
        let node = &tree.nodes[idx as usize];
        let v = self.profile.villain;
        match node.kind {
            KIND_CHANCE => {
                // une carte par classe de cartes équivalentes (couleurs interchangeables), celle que
                // parcourent le solveur et la meilleure réponse ; les autres en sont des copies
                let cs = node.children_start as usize;
                let perms = if solver.use_isomorphism && spot.suit_perms.len() > 1 {
                    spot.perms_fixing(&dealt)
                } else {
                    Vec::new()
                };
                let mut rep = [u8::MAX; 52];
                let mut dealable = 0usize;
                for c in 0..52u8 {
                    if tree.children[cs + c as usize] == SENTINEL || dealt.contains(c) {
                        continue;
                    }
                    dealable += 1;
                    rep[c as usize] = perms.iter().map(|&k| permute_card(c, &spot.suit_perms[k])).fold(c, u8::min);
                }
                for c in 0..52u8 {
                    if rep[c as usize] != c {
                        continue;
                    }
                    let orbit = rep.iter().filter(|&&r| r == c).count();
                    let scale = orbit as f32 / dealable as f32;
                    let bit = 1u64 << c;
                    let next: Vec<f32> = reach
                        .iter()
                        .zip(&spot.hands[v])
                        .map(|(&r, hand)| if hand.mask & bit != 0 { 0.0 } else { r * scale })
                        .collect();
                    self.walk(tree.children[cs + c as usize], &next, dealt.push(c), h.next_street());
                }
            }
            KIND_ACTION => {
                let na = node.num_children as usize;
                let acts = &tree.actions[node.actions_start as usize..node.actions_start as usize + na];
                let actor = node.player as usize;
                let children = &tree.children[node.children_start as usize..node.children_start as usize + na];
                if actor != v {
                    for (a, act) in acts.iter().enumerate() {
                        self.walk(children[a], reach, dealt, h.after(actor, act, v));
                    }
                    return;
                }
                let nh = reach.len();
                let mut sigma = vec![0f32; na * nh];
                if locked_by_user(solver, idx) {
                    solver.average_strategy_into(idx, node, &mut sigma);
                    for (a, act) in acts.iter().enumerate() {
                        let next: Vec<f32> = (0..nh).map(|i| reach[i] * sigma[a * nh + i]).collect();
                        self.walk(children[a], &next, dealt, h.after(actor, act, v));
                    }
                    return;
                }
                solver.solved_strategy_into(node, &mut sigma);
                let facing = acts.iter().any(|a| matches!(a, Action::Fold));
                let (fold, aggressive) = situations(self.profile, &h, node.street, facing);
                let mut changed = Vec::new();
                if na > 1 {
                    let all = vec![true; na];
                    let is_fold: Vec<bool> = acts.iter().map(|a| matches!(a, Action::Fold)).collect();
                    let is_aggressive: Vec<bool> = acts.iter().map(Action::is_aggressive).collect();
                    let not_fold: Vec<bool> = is_fold.iter().map(|&f| !f).collect();
                    let steps = [(fold, &is_fold, &all), (aggressive, &is_aggressive, if facing { &not_fold } else { &all })];
                    for (name, group, within) in steps {
                        let Some(name) = name else { continue };
                        let Some(&ratio) = self.profile.tilts.get(name) else { continue };
                        if !group.iter().any(|&g| g) {
                            continue;
                        }
                        let (before, after, weight) = tilt(&mut sigma, na, nh, reach, group, within, ratio);
                        let t = self.tally.entry(name).or_default();
                        t.mass += weight;
                        t.before += weight * before;
                        t.after += weight * after;
                        t.nodes += 1;
                        changed.push(name);
                    }
                }
                for (a, act) in acts.iter().enumerate() {
                    let next: Vec<f32> = (0..nh).map(|i| reach[i] * sigma[a * nh + i]).collect();
                    self.walk(children[a], &next, dealt, h.after(actor, act, v));
                }
                if !changed.is_empty() {
                    self.locks.push((idx, sigma, format!("{LABEL} {}", changed.join(" + "))));
                }
            }
            _ => {}
        }
    }
}

/// Verrouille les nœuds de l'adversaire sur le profil ; renvoie, par situation, la fréquence du
/// solveur et celle du profil (pondérées par la portée) et le nombre de nœuds verrouillés.
pub fn apply(solver: &mut Solver, profile: &Profile) -> Result<Value, String> {
    if profile.villain > 1 || profile.aggressor > 1 {
        return Err("joueurs attendus : 0 (hors de position) ou 1 (en position)".into());
    }
    if let Some((name, r)) = profile.tilts.iter().find(|(_, r)| !r.is_finite() || **r <= 0.0) {
        return Err(format!("rapport de cotes invalide pour {name} : {r}"));
    }
    let profile = Profile {
        tilts: profile.tilts.iter().map(|(k, &r)| (k.clone(), r.clamp(0.05, 20.0))).collect(),
        ..profile.clone()
    };
    clear(solver);
    let start = std::time::Instant::now();
    let reach = solver.spot.weights[profile.villain].clone();
    let root: f64 = reach.iter().map(|&x| x as f64).sum();
    if root <= 0.0 {
        return Err("la portée de l'adversaire est vide".into());
    }
    let mut walker = Walker { solver, profile: &profile, eps: root * 1e-7, locks: Vec::new(), tally: BTreeMap::new() };
    let history = History { aggressor: profile.aggressor, level: 0, bet_now: false, bet_before: false };
    walker.walk(0, &reach, Dealt::default(), history);
    let Walker { locks, tally, .. } = walker;
    let locked = locks.len();
    for (idx, sigma, label) in locks {
        solver.locks.insert(idx, sigma);
        solver.lock_labels.insert(idx, label);
    }
    // les branches équivalentes reçoivent une copie des nœuds verrouillés (lecture de l'arbre)
    solver.mark_sym_dirty();
    solver.ensure_symmetric();
    let mut out = Map::new();
    for (name, t) in tally {
        if t.mass > 0.0 {
            out.insert(
                name.to_string(),
                json!({"solver": round(t.before / t.mass), "profile": round(t.after / t.mass), "nodes": t.nodes}),
            );
        }
    }
    Ok(json!({"locked": locked, "situations": out, "seconds": round(start.elapsed().as_secs_f64())}))
}

/// Retire les verrous du profil : l'arbre redevient celui du solveur (les verrous de la requête, posés par
/// l'utilisateur, restent).
pub fn clear(solver: &mut Solver) {
    let ours: Vec<u32> = solver.lock_labels.iter().filter(|(_, l)| l.starts_with(LABEL)).map(|(k, _)| *k).collect();
    if ours.is_empty() {
        return;
    }
    for k in ours {
        solver.locks.remove(&k);
        solver.lock_labels.remove(&k);
    }
    solver.mark_sym_dirty();
    solver.ensure_symmetric();
}

/// Un nœud verrouillé par l'utilisateur (pas par un profil) : le profil le laisse tel quel.
fn locked_by_user(solver: &Solver, idx: u32) -> bool {
    solver.lock_labels.get(&idx).is_some_and(|l| !l.starts_with(LABEL))
}

fn round(x: f64) -> f64 {
    (x * 10000.0).round() / 10000.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tilt_moves_the_aggregate_by_the_odds_ratio() {
        // check / mise (sigma[action * nh + main]) : 20 %, 50 %, 80 % de mise, portées égales
        let mut sigma = vec![0.8, 0.5, 0.2, 0.2, 0.5, 0.8];
        let all = [true, true];
        let (before, after, mass) = tilt(&mut sigma, 2, 3, &[1.0, 1.0, 1.0], &[false, true], &all, 3.0);
        assert!((before - 0.5).abs() < 1e-6 && (mass - 3.0).abs() < 1e-6);
        assert!((after - 0.75).abs() < 1e-4, "{after}"); // cotes 1 → 3
        let bets = &sigma[3..];
        assert!(bets[0] < bets[1] && bets[1] < bets[2], "ordre des mains gardé : {bets:?}");
        for i in 0..3 {
            assert!((sigma[i] + sigma[3 + i] - 1.0).abs() < 1e-6);
        }
    }

    #[test]
    fn pure_hands_stay_pure() {
        let mut sigma = vec![1.0, 0.5, 0.0, 0.0, 0.5, 1.0];
        let (_, after, _) = tilt(&mut sigma, 2, 3, &[1.0, 1.0, 1.0], &[false, true], &[true, true], 3.0);
        assert_eq!((sigma[0], sigma[3], sigma[2], sigma[5]), (1.0, 0.0, 0.0, 1.0));
        assert!(after < 0.667 && after > 0.66, "{after}"); // au mieux la main mixte mise toujours
    }

    #[test]
    fn tilt_within_keeps_the_other_actions() {
        // fold / call / relance : la relance ne prend que sur le call, le fold ne bouge pas
        let mut sigma = vec![0.2, 0.4, 0.4];
        let (before, after, _) = tilt(&mut sigma, 3, 1, &[1.0], &[false, false, true], &[false, true, true], 0.25);
        assert!((before - 0.5).abs() < 1e-6 && (after - 0.2).abs() < 1e-4, "{before} {after}");
        assert!((sigma[0] - 0.2).abs() < 1e-6 && (sigma[2] - 0.16).abs() < 1e-4, "{sigma:?}");
    }
}
