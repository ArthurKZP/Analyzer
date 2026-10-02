//! Pont entre Analyzer et le moteur de GTOpen : résout un spot postflop, suit la ligne jouée et,
//! en mode session, répond ensuite aux demandes de navigation dans l'arbre résolu.
//!
//! Usage : analyzer-solve requete.json [--save etude.gz] [--serve] > resultat.json
//!         analyzer-solve --load etude.gz [--serve] > resultat.json
//!
//! Requête :
//!   {"spot": <SpotConfig GTOpen>, "line": [étapes], "max_iterations": 300,
//!    "target_exploit_pct": 0.5, "threads": 0, "max_nodes": 4000000, "gpu": false,
//!    "plan": {"bet:fi:": [{"PotPct": 33.0}], ...}}
//! "plan" (facultatif) : tailles par situation de la ligne (voir arbre.rs) ; l'arbre est alors
//! construit par Analyzer au lieu de GTOpen.
//!   étape = {"action": "check" | "bet" | "call" | "raise" | "fold", "to": 3.5, "allin": false}
//!         | {"card": "Ah"}
//! Les montants sont des totaux de la street, dans l'unité du spot (Analyzer utilise la bb).
//!
//! Sortie (une ligne JSON) : à chaque décision de la ligne, son chemin dans l'arbre, l'action de
//! l'arbre la plus proche de l'action jouée et le nœud complet (voir `NodeOut`). La progression du
//! solveur est écrite sur stderr, une ligne JSON par mesure.
//!
//! Avec --serve, le programme garde ensuite l'arbre en mémoire et lit sur stdin une requête par
//! ligne, {"path": [{"type": "action", "index": 0}, {"type": "card", "card": "Ah"}, ...]}, à laquelle
//! il répond par une ligne {"node": ...} ou {"error": "..."}. Il s'arrête à la fin de stdin.
//!
//! Une étude (--save) garde l'arbre résolu sur disque sous une forme compacte : la requête, puis la
//! stratégie moyenne de chaque nœud sur 8 bits (sans la dernière action, qui s'en déduit), le tout
//! compressé. --load la recharge en quelques secondes, sans recalculer, pour naviguer à nouveau.
//!
//! analyzer-solve --lot lot.json : résout une série de variantes (sous-jeux pour le choix des tailles),
//! en parallèle, sans rien garder ; une ligne JSON par variante, dans l'ordre où elles finissent :
//!   lot = {"threads": 0, "bases": [requête, ...], "runs": [{"base": 0, "plan": {...}}, ...]}
//!   ligne = {"i": 3, "iterations": 60, "exploit_pct": 0.4, "seconds": 0.5, "root_ev": [4.1, 4.2],
//!            "stats": {"bet:ti:i": {"reach": 0.3, "usage": [0.1, 0.05]}, ...}}
//!   stats : pour chaque situation du plan, sa probabilité d'arriver et celle de chacune de ses
//!   tailles (rapportées à la racine).
//! root_ev (aussi dans la sortie normale) : EV de chaque joueur à la racine, part du pot comprise
//! (EV hors de position + EV en position = pot).
//!
//! analyzer-solve --verifier-arbre requete.json : vérifie qu'avec un plan vide, l'arbre d'Analyzer
//! est identique à celui de GTOpen.
//!
//! Compilé avec la fonctionnalité `gpu` (cargo build --features gpu), "gpu": true résout sur une
//! carte NVIDIA via le moteur CUDA de GTOpen, et revient au processeur si la carte n'est pas utilisable.

mod arbre;

use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use solver::query::{ActionView, NodeView};
use solver::tree::{KIND_ACTION, KIND_CHANCE, SENTINEL};
use solver::{PathStep, RunOptions, Solver, Spot, SpotConfig, Storage};
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

#[derive(Deserialize, Clone)]
struct Request {
    spot: SpotConfig,
    #[serde(default)]
    line: Vec<Step>,
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
    #[serde(default)]
    plan: Option<arbre::Plan>,
    /// Sous-jeu : une lettre par street déjà jouée (voir arbre.rs), avec un plan.
    #[serde(default)]
    past: String,
}

/// Le spot de la requête. Avec un plan, GTOpen prépare les ranges et les symétries sur un arbre
/// minimal (sans mise), puis l'arbre d'Analyzer, construit selon le plan, le remplace.
fn make_spot(request: &Request) -> Result<Spot, String> {
    make_spot_tagged(request).map(|(spot, _)| spot)
}

/// `make_spot`, avec les situations du plan rencontrées dans l'arbre (voir arbre::Tag).
fn make_spot_tagged(request: &Request) -> Result<(Spot, Vec<arbre::Tag>), String> {
    let Some(plan) = &request.plan else {
        return Ok((Spot::new_with_limit(request.spot.clone(), Some(request.max_nodes))?, Vec::new()));
    };
    let mut bare = request.spot.clone();
    for sizing in bare.tree.oop.iter_mut().chain(bare.tree.ip.iter_mut()) {
        sizing.bet.clear();
        sizing.raise.clear();
        sizing.donk.clear();
    }
    bare.tree.add_allin = false;
    let mut spot = Spot::new_with_limit(bare, Some(request.max_nodes))?;
    let mut config = request.spot.tree.clone();
    config.carry_aggressor_through_checks = Some(false);
    let hands = [spot.hands[0].len(), spot.hands[1].len()];
    let (tree, tags) = arbre::build(&config, plan, &request.past, spot.board.len(), spot.board_mask, hands, Some(request.max_nodes))?;
    spot.tree = tree;
    spot.config.tree = config;
    Ok((spot, tags))
}

/// Pour chaque situation du plan : sa probabilité d'arriver (les deux joueurs y parviennent, cartes
/// comprises) et celle de chacune de ses tailles, rapportées à la racine.
fn situation_stats(solver: &Solver, tags: &[arbre::Tag]) -> Value {
    let spot = &solver.spot;
    let by_node: std::collections::HashMap<u32, usize> = tags.iter().enumerate().map(|(i, t)| (t.node, i)).collect();
    let mut acc: Vec<(f64, Vec<f64>)> = tags.iter().map(|t| (0.0, vec![0.0; t.sizes])).collect();
    let reach = [spot.weights[0].clone(), spot.weights[1].clone()];
    let root_mass = reach[0].iter().map(|&x| x as f64).sum::<f64>() * reach[1].iter().map(|&x| x as f64).sum::<f64>();
    walk_stats(solver, 0, reach, 1.0, spot.board_mask, &by_node, tags, &mut acc);
    let mut out = serde_json::Map::new();
    for (tag, (mass, usage)) in tags.iter().zip(acc) {
        let entry = out.entry(tag.key.clone()).or_insert_with(|| json!({"reach": 0.0, "usage": vec![0.0; tag.sizes]}));
        entry["reach"] = json!(entry["reach"].as_f64().unwrap_or(0.0) + mass / root_mass);
        for (k, u) in usage.iter().enumerate() {
            entry["usage"][k] = json!(entry["usage"][k].as_f64().unwrap_or(0.0) + u / root_mass);
        }
    }
    Value::Object(out)
}

#[allow(clippy::too_many_arguments)]
fn walk_stats(
    solver: &Solver,
    idx: u32,
    reach: [Vec<f32>; 2],
    chance: f64,
    dealt: u64,
    by_node: &std::collections::HashMap<u32, usize>,
    tags: &[arbre::Tag],
    acc: &mut [(f64, Vec<f64>)],
) {
    let tree = &solver.spot.tree;
    let node = &tree.nodes[idx as usize];
    let mass = |r: &[f32]| r.iter().map(|&x| x as f64).sum::<f64>();
    if mass(&reach[0]) <= 0.0 || mass(&reach[1]) <= 0.0 {
        return;
    }
    if node.kind == KIND_ACTION {
        let p = node.player as usize;
        let nh = reach[p].len();
        let na = node.num_children as usize;
        let sigma = solver.average_strategy(idx, node);
        if let Some(&t) = by_node.get(&idx) {
            let opp = mass(&reach[1 - p]);
            acc[t].0 += chance * mass(&reach[p]) * opp;
            for (a, ids) in tags[t].actions.iter().enumerate() {
                let used: f64 = (0..nh).map(|h| reach[p][h] as f64 * sigma[a * nh + h] as f64).sum();
                for &k in ids {
                    acc[t].1[k] += chance * used * opp;
                }
            }
        }
        for a in 0..na {
            let mut next = reach.clone();
            for h in 0..nh {
                next[p][h] *= sigma[a * nh + h];
            }
            let child = tree.children[node.children_start as usize + a];
            walk_stats(solver, child, next, chance, dealt, by_node, tags, acc);
        }
    } else if node.kind == KIND_CHANCE {
        let start = node.children_start as usize;
        let cards: Vec<u8> = (0..52u8)
            .filter(|&c| tree.children[start + c as usize] != SENTINEL && dealt & (1u64 << c) == 0)
            .collect();
        let share = chance / cards.len().max(1) as f64;
        for c in cards {
            let mut next = reach.clone();
            for p in 0..2 {
                for (h, info) in solver.spot.hands[p].iter().enumerate() {
                    if info.mask & (1u64 << c) != 0 {
                        next[p][h] = 0.0;
                    }
                }
            }
            walk_stats(solver, tree.children[start + c as usize], next, share, dealt | (1u64 << c), by_node, tags, acc);
        }
    }
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

#[derive(Deserialize, Clone)]
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

#[derive(Deserialize)]
struct Query {
    path: Vec<PathStep>,
}

#[derive(Serialize)]
struct ActionOut {
    label: String,
    kind: String,
    amount: f64,
    allin: bool,
}

#[derive(Serialize)]
struct HistOut {
    kind: String,
    player: Option<u8>,
    stack: f64,
    pot: f64,
    /// Jetons engagés par chaque joueur depuis le début du coup (pour les relances en % du pot).
    put: Option<[f64; 2]>,
    street: u8,
    actions: Vec<ActionOut>,
    chosen: Option<usize>,
    card: Option<String>,
}

/// Un nœud de l'arbre. `hands[p]` : une liste par joueur (0 = hors de position), une entrée par
/// main encore présente, [main, présence, équité, EV, stratégie…, EV de chaque action…] ; la
/// stratégie et l'EV par action ne concernent que le joueur qui agit. Montants en unités du spot.
#[derive(Serialize)]
struct NodeOut {
    #[serde(rename = "type")]
    kind: String,
    street: u8,
    board: Vec<String>,
    pot: f64,
    put: [f64; 2],
    stacks: [f64; 2],
    player: Option<u8>,
    actions: Vec<ActionOut>,
    cards: Option<Vec<String>>,
    hands: [Vec<Value>; 2],
    history: Vec<HistOut>,
}

#[derive(Serialize)]
struct Decision {
    step: usize,
    path: Vec<PathStep>,
    chosen: Option<usize>,
    node: NodeOut,
}

#[derive(Serialize)]
struct Output {
    engine: &'static str,
    iterations: u32,
    exploit_pct: f64,
    seconds: f64,
    tree_nodes: usize,
    root_ev: [f64; 2],
    decisions: Vec<Decision>,
    stopped: Option<String>,
}

/// EV de chaque joueur à la racine : moyenne de ses mains pondérée par présence × masse adverse
/// compatible (la convention de GTOpen, pour que EV hors de position + EV en position = pot).
fn root_ev(solver: &Solver) -> [f64; 2] {
    let Ok(view) = solver.node_view(&[]) else { return [f64::NAN; 2] };
    [0usize, 1].map(|p| {
        let (mut num, mut den) = (0.0, 0.0);
        for h in &view.players[p].hands {
            if let Some(ev) = h.ev {
                let w = h.reach as f64 * h.valid as f64;
                num += w * ev as f64;
                den += w;
            }
        }
        if den > 0.0 { round(num / den, 5) } else { f64::NAN }
    })
}

#[derive(Deserialize)]
struct Lot {
    #[serde(default)]
    threads: usize,
    bases: Vec<Request>,
    runs: Vec<Run>,
}

#[derive(Deserialize)]
struct Run {
    base: usize,
    #[serde(default)]
    plan: arbre::Plan,
}

/// Résout chaque variante du lot (en parallèle) et écrit son résultat dès qu'elle finit.
fn run_lot(path: &str) {
    use rayon::prelude::*;
    let text = std::fs::read_to_string(path).unwrap_or_else(|e| fail(format!("lecture de {path} : {e}")));
    let lot: Lot = serde_json::from_str(&text).unwrap_or_else(|e| fail(format!("lot invalide : {e}")));
    if lot.threads > 0 {
        rayon::ThreadPoolBuilder::new().num_threads(lot.threads).build_global().ok();
    }
    let out = std::sync::Mutex::new(std::io::stdout());
    lot.runs.par_iter().enumerate().for_each(|(i, run)| {
        let reply = match lot.bases.get(run.base) {
            None => json!({ "i": i, "error": "base inconnue" }),
            Some(base) => {
                let mut request = base.clone();
                request.plan = Some(run.plan.clone());
                match make_spot_tagged(&request) {
                    Err(e) => json!({ "i": i, "error": e }),
                    Ok((spot, tags)) => {
                        let mut solver = Solver::with_storage(Arc::new(spot), Storage::Compressed);
                        let opts = RunOptions {
                            max_iterations: request.max_iterations,
                            target_exploit_pct: request.target_exploit_pct,
                            check_every: 10,
                        };
                        let stop = AtomicBool::new(false);
                        let p = solver.run(&opts, &stop, |_| {});
                        solver.ensure_symmetric();
                        json!({
                            "i": i, "iterations": p.iteration, "exploit_pct": round(p.exploit_pct_pot, 3),
                            "seconds": round(p.elapsed_secs, 2), "tree_nodes": solver.spot.tree.nodes.len(),
                            "root_ev": root_ev(&solver), "stats": situation_stats(&solver, &tags),
                        })
                    }
                }
            }
        };
        let mut out = out.lock().unwrap();
        let _ = writeln!(out, "{reply}").and_then(|_| out.flush());
    });
}

fn round(x: f64, digits: i32) -> f64 {
    let f = 10f64.powi(digits);
    (x * f).round() / f
}

fn opt(x: Option<f32>, digits: i32) -> Value {
    x.map_or(Value::Null, |v| json!(round(v as f64, digits)))
}

fn actions_out(actions: &[ActionView]) -> Vec<ActionOut> {
    actions
        .iter()
        .map(|a| ActionOut {
            label: a.label.clone(),
            kind: a.kind.clone(),
            amount: round(a.amount, 3),
            allin: a.label.starts_with("All-in"),
        })
        .collect()
}

fn node_out(solver: &Solver, view: &NodeView) -> NodeOut {
    let config = &solver.spot.tree.config;
    let behind = |p: usize| round(config.effective_stack - (view.put[p] - config.starting_pot / 2.0), 3);
    let hands = [0usize, 1].map(|p| {
        view.players[p]
            .hands
            .iter()
            .filter(|h| h.reach > 1e-6)
            .map(|h| {
                let mut row = vec![json!(h.combo), json!(round(h.reach as f64, 4)), opt(h.eq, 3), opt(h.ev, 3)];
                if let Some(s) = &h.strategy {
                    row.extend(s.iter().map(|&x| json!(round(x as f64, 3))));
                }
                if let Some(e) = &h.evs {
                    row.extend(e.iter().map(|&x| opt(x, 3)));
                }
                Value::Array(row)
            })
            .collect()
    });
    NodeOut {
        kind: view.node_type.clone(),
        street: view.street,
        board: view.board.clone(),
        pot: round(view.pot, 3),
        put: [round(view.put[0], 3), round(view.put[1], 3)],
        stacks: [behind(0), behind(1)],
        player: view.player,
        actions: actions_out(&view.actions),
        cards: view.available_cards.clone(),
        hands,
        history: view
            .history
            .iter()
            .map(|h| HistOut {
                kind: h.kind.clone(),
                player: h.player,
                stack: round(h.stack, 3),
                pot: round(h.pot, 3),
                put: h.player.map(|p| {
                    let mine = config.effective_stack + config.starting_pot / 2.0 - h.stack;
                    let mut put = [round(h.pot - mine, 3); 2];
                    put[p as usize] = round(mine, 3);
                    put
                }),
                street: h.street,
                actions: actions_out(&h.actions),
                chosen: h.chosen,
                card: h.card.clone(),
            })
            .collect(),
    }
}

fn report(iteration: u32, exploit_pct: f64, elapsed: f64) {
    let _ = writeln!(
        std::io::stderr(),
        "{}",
        json!({"iteration": iteration, "exploit_pct": round(exploit_pct, 3), "elapsed": round(elapsed, 1)})
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
    let _ = writeln!(std::io::stderr(), "{}", json!({ "error": message }));
    std::process::exit(2);
}

/// Indice de l'action de l'arbre la plus proche de l'action réellement jouée.
fn match_action(actions: &[ActionView], kind: &str, to: f64, allin: bool) -> Option<usize> {
    let wanted: &[&str] = match kind {
        "bet" | "raise" => &["bet", "raise"],
        "call" => &["call"],
        "check" => &["check"],
        "fold" => &["fold"],
        _ => return None,
    };
    let candidates: Vec<usize> = (0..actions.len()).filter(|&i| wanted.contains(&actions[i].kind.as_str())).collect();
    if allin {
        if let Some(&i) = candidates.iter().find(|&&i| actions[i].label.starts_with("All-in")) {
            return Some(i);
        }
    }
    candidates.into_iter().min_by(|&a, &b| {
        let da = (actions[a].amount - to).abs();
        let db = (actions[b].amount - to).abs();
        da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
    })
}

/// Suit la ligne jouée et exporte chaque décision.
fn follow_line(solver: &Solver, line: &[Step]) -> (Vec<Decision>, Option<String>) {
    let mut decisions = Vec::new();
    let mut path: Vec<PathStep> = Vec::new();
    for (index, step) in line.iter().enumerate() {
        let view = match solver.node_view(&path) {
            Ok(v) => v,
            Err(e) => return (decisions, Some(e)),
        };
        match step {
            Step::Card { card } => {
                if view.node_type != "chance" {
                    return (decisions, Some(format!("carte {card} attendue mais le nœud est {}", view.node_type)));
                }
                path.push(PathStep::Card { card: card.clone() });
            }
            Step::Action { action, to, allin } => {
                if view.node_type != "action" {
                    return (decisions, Some(format!("action {action} attendue mais le nœud est {}", view.node_type)));
                }
                let chosen = match_action(&view.actions, action, *to, *allin);
                decisions.push(Decision { step: index, path: path.clone(), chosen, node: node_out(solver, &view) });
                match chosen {
                    Some(i) => path.push(PathStep::Action { index: i }),
                    None => return (decisions, Some(format!("action {action} absente de l'arbre à ce nœud"))),
                }
            }
        }
    }
    (decisions, None)
}

const STUDY_MAGIC: &[u8] = b"ANALYZER-ETUDE1\n";

/// Enregistre l'étude : en-tête JSON (requête, résumé), puis les stratégies moyennes sur 8 bits.
fn save_study(solver: &Solver, request: &Value, summary: &Value, path: &str) -> Result<(), String> {
    let err = |e: std::io::Error| e.to_string();
    let tmp = format!("{path}.tmp");
    let mut w = BufWriter::new(std::fs::File::create(&tmp).map_err(err)?);
    w.write_all(STUDY_MAGIC).map_err(err)?;
    writeln!(w, "{}", json!({ "request": request, "summary": summary })).map_err(err)?;
    let mut z = GzEncoder::new(w, Compression::fast());
    let tree = &solver.spot.tree;
    for (idx, node) in tree.nodes.iter().enumerate() {
        if node.kind != KIND_ACTION {
            continue;
        }
        let nh = solver.spot.hands[node.player as usize].len();
        let na = node.num_children as usize;
        let sigma = solver.average_strategy(idx as u32, node);
        let block: Vec<u8> = sigma[..(na - 1) * nh].iter().map(|&x| (x.clamp(0.0, 1.0) * 255.0).round() as u8).collect();
        z.write_all(&block).map_err(err)?;
    }
    let mut w = z.finish().map_err(err)?;
    w.flush().map_err(err)?;
    drop(w);
    std::fs::rename(&tmp, path).map_err(err)
}

/// Recharge une étude : la requête d'origine, le résumé de la résolution et le solveur rempli.
fn load_study(path: &str) -> Result<(Request, Value, Solver), String> {
    let err = |e: std::io::Error| format!("lecture de l'étude {path} : {e}");
    let mut r = BufReader::new(std::fs::File::open(path).map_err(err)?);
    let mut magic = vec![0u8; STUDY_MAGIC.len()];
    r.read_exact(&mut magic).map_err(err)?;
    if magic != STUDY_MAGIC {
        return Err(format!("{path} n'est pas une étude d'Analyzer"));
    }
    let mut line = String::new();
    r.read_line(&mut line).map_err(err)?;
    let header: Value = serde_json::from_str(&line).map_err(|e| format!("en-tête d'étude invalide : {e}"))?;
    let request: Request = serde_json::from_value(header["request"].clone()).map_err(|e| format!("requête invalide : {e}"))?;
    let spot = make_spot(&request)?;
    let solver = Solver::with_storage(Arc::new(spot), Storage::Compressed);
    let mut z = GzDecoder::new(r);
    let tree = &solver.spot.tree;
    for (idx, node) in tree.nodes.iter().enumerate() {
        if node.kind != KIND_ACTION {
            continue;
        }
        let p = node.player as usize;
        let nh = solver.spot.hands[p].len();
        let na = node.num_children as usize;
        let mut block = vec![0u8; (na - 1) * nh];
        z.read_exact(&mut block).map_err(err)?;
        let mut sums = vec![0f32; na * nh];
        for i in 0..nh {
            let mut rest = 255.0f32;
            for a in 0..na - 1 {
                let v = block[a * nh + i] as f32;
                sums[a * nh + i] = v;
                rest -= v;
            }
            sums[(na - 1) * nh + i] = rest.max(0.0);
        }
        // Les sommes de stratégie suffisent pour lire la stratégie moyenne (les regrets ne servent
        // qu'à poursuivre le calcul).
        unsafe { solver.strat[p].write_f32(idx as u32, node.data_offset, na * nh, &sums) };
    }
    Ok((request, header["summary"].clone(), solver))
}

fn serve(solver: &Solver) {
    let stdin = std::io::stdin();
    let mut out = std::io::stdout();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let reply = match serde_json::from_str::<Query>(&line) {
            Ok(q) => match solver.node_view(&q.path) {
                Ok(view) => json!({ "node": node_out(solver, &view) }),
                Err(e) => json!({ "error": e }),
            },
            Err(e) => json!({ "error": format!("requête invalide : {e}") }),
        };
        if writeln!(out, "{reply}").and_then(|_| out.flush()).is_err() {
            break;
        }
    }
}

fn option(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let session = args.iter().any(|a| a == "--serve");
    if let Some(path) = option(&args, "--lot") {
        run_lot(&path);
        return;
    }
    if let Some(path) = option(&args, "--verifier-arbre") {
        let text = std::fs::read_to_string(&path).unwrap_or_else(|e| fail(format!("lecture de {path} : {e}")));
        let mut request: Request = serde_json::from_str(&text).unwrap_or_else(|e| fail(format!("requête invalide : {e}")));
        request.plan = None;
        let gtopen = make_spot(&request).unwrap_or_else(|e| fail(e));
        request.plan = Some(arbre::Plan::new());
        let ours = make_spot(&request).unwrap_or_else(|e| fail(e));
        let reply = match arbre::same_tree(&gtopen.tree, &ours.tree) {
            Ok(()) => json!({ "identique": true, "noeuds": ours.tree.nodes.len() }),
            Err(e) => json!({ "identique": false, "difference": e }),
        };
        println!("{reply}");
        return;
    }
    if let Some(study) = option(&args, "--load") {
        let start = std::time::Instant::now();
        let (request, summary, solver) = load_study(&study).unwrap_or_else(|e| fail(e));
        let _ = writeln!(std::io::stderr(), "{}", json!({ "loaded": round(start.elapsed().as_secs_f64(), 1) }));
        let (decisions, stopped) = follow_line(&solver, &request.line);
        let out = Output {
            engine: "etude",
            iterations: summary["iterations"].as_u64().unwrap_or(0) as u32,
            exploit_pct: summary["exploit_pct"].as_f64().unwrap_or(f64::NAN),
            seconds: summary["seconds"].as_f64().unwrap_or(0.0),
            tree_nodes: solver.spot.tree.nodes.len(),
            root_ev: root_ev(&solver),
            decisions,
            stopped,
        };
        println!("{}", serde_json::to_string(&out).expect("sérialisation du résultat"));
        let _ = std::io::stdout().flush();
        if session {
            serve(&solver);
        }
        return;
    }
    let path = args.get(1).cloned().unwrap_or_else(|| fail("usage : analyzer-solve requete.json [--save etude.gz] [--serve]".into()));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| fail(format!("lecture de {path} : {e}")));
    let request: Request = serde_json::from_str(&text).unwrap_or_else(|e| fail(format!("requête invalide : {e}")));
    let request_value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    if request.threads > 0 {
        rayon::ThreadPoolBuilder::new().num_threads(request.threads).build_global().ok();
    }

    let spot = make_spot(&request).unwrap_or_else(|e| fail(e));
    let tree_nodes = spot.tree.nodes.len();
    // Le moteur GPU travaille en précision complète (f32).
    let storage = if request.gpu { Storage::F32 } else { Storage::Compressed };
    let mut solver = Solver::with_storage(Arc::new(spot), storage);
    let _ = writeln!(
        std::io::stderr(),
        "{}",
        json!({"tree_nodes": tree_nodes, "arena_mb": round(solver.arena_bytes() as f64 / 1e6, 1)})
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
                let _ = writeln!(std::io::stderr(), "{}", json!({ "warning": format!("GPU indisponible ({e}) : résolution sur le processeur") }));
            }
        }
    }
    let (iterations, exploit_pct, seconds) = done.unwrap_or_else(|| {
        let stop = AtomicBool::new(false);
        let p = solver.run(&opts, &stop, |p| report(p.iteration, p.exploit_pct_pot, p.elapsed_secs));
        (p.iteration, p.exploit_pct_pot, p.elapsed_secs)
    });
    solver.ensure_symmetric();

    if let Some(study) = option(&args, "--save") {
        let start = std::time::Instant::now();
        let summary = json!({"iterations": iterations, "exploit_pct": round(exploit_pct, 3), "seconds": round(seconds, 1)});
        match save_study(&solver, &request_value, &summary, &study) {
            Ok(()) => {
                let _ = writeln!(std::io::stderr(), "{}", json!({ "saved": round(start.elapsed().as_secs_f64(), 1) }));
            }
            Err(e) => {
                let _ = writeln!(std::io::stderr(), "{}", json!({ "warning": format!("étude non enregistrée : {e}") }));
            }
        }
    }

    let (decisions, stopped) = follow_line(&solver, &request.line);
    let out = Output {
        engine,
        iterations,
        exploit_pct: round(exploit_pct, 3),
        seconds: round(seconds, 1),
        tree_nodes,
        root_ev: root_ev(&solver),
        decisions,
        stopped,
    };
    println!("{}", serde_json::to_string(&out).expect("sérialisation du résultat"));
    let _ = std::io::stdout().flush();
    if session {
        serve(&solver);
    }
}
