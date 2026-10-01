//! Pont entre Analyzer et le moteur de GTOpen : résout un spot postflop puis suit la ligne jouée.
//!
//! Usage : analyzer-solve requete.json > resultat.json
//!
//! Requête :
//!   {"spot": <SpotConfig GTOpen>, "line": [étapes], "combos": {"AhKd": 1, ...} (main -> joueur, 0 = OOP),
//!    "max_iterations": 300, "target_exploit_pct": 0.5, "threads": 0, "max_nodes": 4000000,
//!    "gpu": false}
//!   étape = {"action": "check" | "bet" | "call" | "raise" | "fold", "to": 3.5, "allin": false}
//!         | {"card": "Ah"}
//! Les montants sont des totaux de la street, dans l'unité du spot (Analyzer utilise la bb).
//!
//! Sortie : à chaque décision de la ligne, les actions de l'arbre, la fréquence de chacune pour
//! toute la range du joueur, la stratégie par classe de main (AKs, 72o…) et, pour les combos
//! demandés, la stratégie, l'EV de chaque action et l'équité. La progression du solveur est écrite
//! sur stderr, une ligne JSON par mesure.
//!
//! Compilé avec la fonctionnalité `gpu` (cargo build --features gpu), "gpu": true résout sur une
//! carte NVIDIA via le moteur CUDA de GTOpen, et revient au processeur si la carte n'est pas utilisable.

use serde::{Deserialize, Serialize};
use solver::cards::{card_from_str, combo_index, rank, suit, Card, RANK_CHARS};
use solver::{PathStep, RunOptions, Solver, Spot, SpotConfig, Storage};
use std::collections::BTreeMap;
use std::io::Write;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

#[derive(Deserialize)]
struct Request {
    spot: SpotConfig,
    #[serde(default)]
    line: Vec<Step>,
    #[serde(default)]
    combos: BTreeMap<String, u8>,
    #[serde(default = "default_iterations")]
    max_iterations: u32,
    #[serde(default = "default_target")]
    target_exploit_pct: f64,
    #[serde(default)]
    threads: usize,
    #[serde(default = "default_max_nodes")]
    max_nodes: usize,
    #[serde(default)]
    gpu: bool,
}

fn default_iterations() -> u32 {
    300
}
fn default_target() -> f64 {
    0.5
}
fn default_max_nodes() -> usize {
    4_000_000
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Step {
    Card {
        card: String,
    },
    Action {
        action: String,
        #[serde(default)]
        to: f64,
        #[serde(default)]
        allin: bool,
    },
}

#[derive(Serialize)]
struct ActionOut {
    label: String,
    kind: String,
    amount: f64,
    allin: bool,
}

#[derive(Serialize)]
struct ClassOut {
    /// Somme des probabilités d'atteindre ce nœud sur les combos de la classe.
    w: f64,
    /// Nombre de combos de la classe compatibles avec le board.
    n: u32,
    /// Stratégie moyenne de la classe (pondérée par la probabilité d'atteindre le nœud).
    s: Vec<f64>,
}

#[derive(Serialize)]
struct ComboOut {
    player: u8,
    reach: f64,
    eq: Option<f64>,
    ev: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    s: Option<Vec<f64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    evs: Option<Vec<Option<f64>>>,
}

#[derive(Serialize)]
struct Decision {
    step: usize,
    street: u8,
    board: Vec<String>,
    pot: f64,
    player: u8,
    actions: Vec<ActionOut>,
    chosen: Option<usize>,
    range: Vec<f64>,
    classes: BTreeMap<String, ClassOut>,
    combos: BTreeMap<String, ComboOut>,
}

#[derive(Serialize)]
struct Output {
    engine: &'static str,
    iterations: u32,
    exploit_pct: f64,
    seconds: f64,
    tree_nodes: usize,
    decisions: Vec<Decision>,
    stopped: Option<String>,
}

fn round(x: f64, digits: i32) -> f64 {
    let f = 10f64.powi(digits);
    (x * f).round() / f
}

fn class_of(c1: Card, c2: Card) -> String {
    let (hi, lo) = if rank(c1) >= rank(c2) { (c1, c2) } else { (c2, c1) };
    let (h, l) = (RANK_CHARS[rank(hi) as usize], RANK_CHARS[rank(lo) as usize]);
    if rank(hi) == rank(lo) {
        format!("{h}{l}")
    } else if suit(hi) == suit(lo) {
        format!("{h}{l}s")
    } else {
        format!("{h}{l}o")
    }
}

fn parse_combo(s: &str) -> Option<usize> {
    let s = s.trim();
    if s.len() != 4 {
        return None;
    }
    let a = card_from_str(&s[0..2]).ok()?;
    let b = card_from_str(&s[2..4]).ok()?;
    (a != b).then(|| combo_index(a, b))
}

fn report(iteration: u32, exploit_pct: f64, elapsed: f64) {
    let _ = writeln!(
        std::io::stderr(),
        "{}",
        serde_json::json!({"iteration": iteration, "exploit_pct": round(exploit_pct, 3), "elapsed": round(elapsed, 1)})
    );
}

/// Résolution sur la carte graphique : (itérations, exploitabilité en % du pot, secondes).
#[cfg(feature = "gpu")]
fn solve_gpu(solver: &mut Solver, opts: &RunOptions) -> Result<(u32, f64, f64), String> {
    let pot = solver.spot.tree.config.starting_pot;
    let start = std::time::Instant::now();
    let mut gpu = solver::gpu::GpuSolver::new(solver)?;
    loop {
        gpu.iterate()?;
        if gpu.iteration % opts.check_every.max(1) == 0 || gpu.iteration >= opts.max_iterations {
            let pct = gpu.exploitability(solver)? / pot * 100.0;
            let elapsed = start.elapsed().as_secs_f64();
            report(gpu.iteration, pct, elapsed);
            if pct <= opts.target_exploit_pct || gpu.iteration >= opts.max_iterations {
                gpu.sync_to_cpu(solver)?;
                return Ok((gpu.iteration, pct, elapsed));
            }
        }
    }
}

#[cfg(not(feature = "gpu"))]
fn solve_gpu(_solver: &mut Solver, _opts: &RunOptions) -> Result<(u32, f64, f64), String> {
    Err("programme compilé sans la fonctionnalité gpu".into())
}

fn fail(message: String) -> ! {
    let _ = writeln!(std::io::stderr(), "{}", serde_json::json!({ "error": message }));
    std::process::exit(2);
}

/// Indice de l'action de l'arbre la plus proche de l'action réellement jouée.
fn match_action(actions: &[ActionOut], kind: &str, to: f64, allin: bool) -> Option<usize> {
    let wanted: &[&str] = match kind {
        "bet" | "raise" => &["bet", "raise"],
        "call" => &["call"],
        "check" => &["check"],
        "fold" => &["fold"],
        _ => return None,
    };
    let candidates: Vec<usize> = (0..actions.len()).filter(|&i| wanted.contains(&actions[i].kind.as_str())).collect();
    if allin {
        if let Some(&i) = candidates.iter().find(|&&i| actions[i].allin) {
            return Some(i);
        }
    }
    candidates.into_iter().min_by(|&a, &b| {
        let da = (actions[a].amount - to).abs();
        let db = (actions[b].amount - to).abs();
        da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
    })
}

fn main() {
    let path = std::env::args().nth(1).unwrap_or_else(|| fail("usage : analyzer-solve requete.json".into()));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| fail(format!("lecture de {path} : {e}")));
    let request: Request = serde_json::from_str(&text).unwrap_or_else(|e| fail(format!("requête invalide : {e}")));
    if request.threads > 0 {
        rayon::ThreadPoolBuilder::new().num_threads(request.threads).build_global().ok();
    }

    let spot = Spot::new_with_limit(request.spot, Some(request.max_nodes)).unwrap_or_else(|e| fail(e));
    let tree_nodes = spot.tree.nodes.len();
    // Le moteur GPU travaille en précision complète (f32).
    let storage = if request.gpu { Storage::F32 } else { Storage::Compressed };
    let mut solver = Solver::with_storage(Arc::new(spot), storage);
    let _ = writeln!(
        std::io::stderr(),
        "{}",
        serde_json::json!({"tree_nodes": tree_nodes, "arena_mb": round(solver.arena_bytes() as f64 / 1e6, 1)})
    );

    let opts = RunOptions {
        max_iterations: request.max_iterations,
        target_exploit_pct: request.target_exploit_pct,
        check_every: 10,
    };
    let mut engine = "cpu";
    let mut done = None;
    if request.gpu {
        match solve_gpu(&mut solver, &opts) {
            Ok(result) => {
                engine = "gpu";
                done = Some(result);
            }
            Err(e) => {
                let _ = writeln!(std::io::stderr(), "{}", serde_json::json!({ "warning": format!("GPU indisponible ({e}) : résolution sur le processeur") }));
            }
        }
    }
    let (iterations, exploit_pct, seconds) = done.unwrap_or_else(|| {
        let stop = AtomicBool::new(false);
        let p = solver.run(&opts, &stop, |p| report(p.iteration, p.exploit_pct_pot, p.elapsed_secs));
        (p.iteration, p.exploit_pct_pot, p.elapsed_secs)
    });
    solver.ensure_symmetric();

    let wanted: Vec<(String, usize, u8)> = request
        .combos
        .iter()
        .filter_map(|(c, &p)| parse_combo(c).map(|i| (c.clone(), i, p.min(1))))
        .collect();
    let mut decisions = Vec::new();
    let mut path: Vec<PathStep> = Vec::new();
    let mut stopped = None;
    for (index, step) in request.line.iter().enumerate() {
        let view = match solver.node_view(&path) {
            Ok(v) => v,
            Err(e) => {
                stopped = Some(e);
                break;
            }
        };
        match step {
            Step::Card { card } => {
                if view.node_type != "chance" {
                    stopped = Some(format!("carte {card} attendue mais le nœud est {}", view.node_type));
                    break;
                }
                path.push(PathStep::Card { card: card.clone() });
            }
            Step::Action { action, to, allin } => {
                if view.node_type != "action" {
                    stopped = Some(format!("action {action} attendue mais le nœud est {}", view.node_type));
                    break;
                }
                let player = view.player.unwrap_or(0);
                let actions: Vec<ActionOut> = view
                    .actions
                    .iter()
                    .map(|a| ActionOut {
                        label: a.label.clone(),
                        kind: a.kind.clone(),
                        amount: round(a.amount, 3),
                        allin: a.label.starts_with("All-in"),
                    })
                    .collect();
                let na = actions.len();
                let chosen = match_action(&actions, action, *to, *allin);

                let mut range = vec![0f64; na];
                let mut total = 0f64;
                let mut classes: BTreeMap<String, ClassOut> = BTreeMap::new();
                for h in &view.players[player as usize].hands {
                    let entry = classes.entry(class_of(h.c1, h.c2)).or_insert(ClassOut { w: 0.0, n: 0, s: vec![0.0; na] });
                    entry.n += 1;
                    let reach = h.reach as f64;
                    if reach <= 0.0 {
                        continue;
                    }
                    if let Some(strategy) = &h.strategy {
                        entry.w += reach;
                        total += reach;
                        for a in 0..na {
                            entry.s[a] += reach * strategy[a] as f64;
                            range[a] += reach * strategy[a] as f64;
                        }
                    }
                }
                for c in classes.values_mut() {
                    if c.w > 0.0 {
                        c.s = c.s.iter().map(|x| round(x / c.w, 3)).collect();
                    }
                    c.w = round(c.w, 4);
                }
                if total > 0.0 {
                    range = range.iter().map(|x| round(x / total, 4)).collect();
                }

                let mut combos = BTreeMap::new();
                for (name, idx, p) in &wanted {
                    let found = view.players[*p as usize].hands.iter().find(|h| combo_index(h.c1, h.c2) == *idx);
                    if let Some(h) = found {
                        combos.insert(
                            name.clone(),
                            ComboOut {
                                player: *p,
                                reach: round(h.reach as f64, 5),
                                eq: h.eq.map(|x| round(x as f64, 4)),
                                ev: h.ev.map(|x| round(x as f64, 3)),
                                s: h.strategy.as_ref().map(|s| s.iter().map(|&x| round(x as f64, 4)).collect()),
                                evs: h
                                    .evs
                                    .as_ref()
                                    .map(|e| e.iter().map(|x| x.map(|v| round(v as f64, 3))).collect()),
                            },
                        );
                    }
                }

                decisions.push(Decision {
                    step: index,
                    street: view.street,
                    board: view.board.clone(),
                    pot: round(view.pot, 3),
                    player,
                    actions,
                    chosen,
                    range,
                    classes,
                    combos,
                });
                match chosen {
                    Some(i) => path.push(PathStep::Action { index: i }),
                    None => {
                        stopped = Some(format!("action {action} absente de l'arbre à ce nœud"));
                        break;
                    }
                }
            }
        }
    }

    let out = Output {
        engine,
        iterations,
        exploit_pct: round(exploit_pct, 3),
        seconds: round(seconds, 1),
        tree_nodes,
        decisions,
        stopped,
    };
    println!("{}", serde_json::to_string(&out).expect("sérialisation du résultat"));
}
