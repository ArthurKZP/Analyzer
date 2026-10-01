//! Pont entre Analyzer et le moteur de GTOpen : résout un spot postflop, suit la ligne jouée et,
//! en mode session, répond ensuite aux demandes de navigation dans l'arbre résolu.
//!
//! Usage : analyzer-solve requete.json [--serve] > resultat.json
//!
//! Requête :
//!   {"spot": <SpotConfig GTOpen>, "line": [étapes], "max_iterations": 300,
//!    "target_exploit_pct": 0.5, "threads": 0, "max_nodes": 4000000, "gpu": false}
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
//! Compilé avec la fonctionnalité `gpu` (cargo build --features gpu), "gpu": true résout sur une
//! carte NVIDIA via le moteur CUDA de GTOpen, et revient au processeur si la carte n'est pas utilisable.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use solver::query::{ActionView, NodeView};
use solver::{PathStep, RunOptions, Solver, Spot, SpotConfig, Storage};
use std::io::{BufRead, Write};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

#[derive(Deserialize)]
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
    decisions: Vec<Decision>,
    stopped: Option<String>,
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

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let path = args.get(1).cloned().unwrap_or_else(|| fail("usage : analyzer-solve requete.json [--serve]".into()));
    let session = args.iter().any(|a| a == "--serve");
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

    let (decisions, stopped) = follow_line(&solver, &request.line);
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
    let _ = std::io::stdout().flush();
    if session {
        serve(&solver);
    }
}
