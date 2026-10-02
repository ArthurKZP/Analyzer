//! Arbre de jeu d'Analyzer : les règles du constructeur de GTOpen (mêmes nœuds, mêmes montants, même
//! ordre), avec des tailles de mise qui dépendent de la ligne jouée et non seulement de la street.
//!
//! Le plan associe une situation à sa liste de tailles. Clé d'une situation :
//!
//!   "bet:<street><joueur>:<passé>"            première mise de la street
//!   "raise:<street><joueur>:<passé>:<niveau>"  relance (niveau = relances déjà faites dans la street)
//!
//!   street : f, t, r ; joueur : o (hors de position) ou i (en position) ;
//!   passé  : une lettre par street terminée, o / i pour son dernier agresseur, x si elle a été checkée.
//!
//! Exemples (SRP, le bouton en position) : "bet:fi:" c-bet ; "bet:ti:i" 2e barrel ; "bet:ti:x" c-bet
//! retardée ; "bet:to:x" probe turn ; "bet:ri:ix" bet/check/bet ; "bet:ro:ix" probe river ;
//! "raise:fo::0" check-raise au flop ; "raise:fi::1" relance du bouton face au check-raise.
//!
//! Une taille du plan : un nombre (% du pot ; pour une relance, du pot après le call), "geo" (la
//! même fraction du pot à chaque street restante pour finir à tapis à la river : à la river, le
//! tapis) ou "a" (tapis).
//!
//! Une situation absente du plan prend les tailles de sa street dans la configuration de GTOpen
//! (bet, donk quand la BB mène dans l'agresseur de la street précédente, raise) : un plan vide
//! redonne exactement l'arbre de GTOpen (voir `same_tree`).
//!
//! Un sous-jeu (board de 4 ou 5 cartes, ranges du nœud d'origine) reçoit le passé des streets déjà
//! jouées (`root_past`, ex. "i" à la turn après une c-bet payée) : les clés et la règle du donk
//! s'appliquent alors comme dans l'arbre complet.

use serde::de::{Deserializer, Error};
use serde::Deserialize;
use solver::tree::{
    Action, BetSize, Node, Tree, TreeConfig, IP, KIND_ACTION, KIND_CHANCE, KIND_TERM_FOLD,
    KIND_TERM_SHOWDOWN, OOP, SENTINEL,
};
use std::collections::HashMap;

pub type Plan = HashMap<String, Vec<Size>>;

/// Taille d'une mise ou d'une relance (voir l'en-tête). `Mult` ne vient que de la configuration de
/// GTOpen (relance en multiple de la mise adverse).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Size {
    Pct(f64),
    Mult(f64),
    Geo,
    AllIn,
}

impl<'de> Deserialize<'de> for Size {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Raw {
            Num(f64),
            Text(String),
        }
        match Raw::deserialize(d)? {
            Raw::Num(p) if p.is_finite() && p > 0.0 => Ok(Size::Pct(p)),
            Raw::Text(t) if t == "geo" => Ok(Size::Geo),
            Raw::Text(t) if t == "a" => Ok(Size::AllIn),
            _ => Err(D::Error::custom("taille invalide : un % du pot, \"geo\" ou \"a\"")),
        }
    }
}

impl From<&BetSize> for Size {
    fn from(size: &BetSize) -> Size {
        match *size {
            BetSize::PotPct(p) => Size::Pct(p),
            BetSize::PrevMult(m) => Size::Mult(m),
            BetSize::AllIn => Size::AllIn,
        }
    }
}

/// Une situation du plan rencontrée dans l'arbre : son nœud, sa clé et, pour chaque action du nœud,
/// les tailles du plan (indices dans sa liste) qui y mènent (geo et tapis peuvent se confondre).
pub struct Tag {
    pub node: u32,
    pub key: String,
    pub sizes: usize,
    pub actions: Vec<Vec<usize>>,
}

/// Fraction du pot à miser à chaque street restante (`streets`, celle-ci comprise) pour engager
/// `stack` dans un pot `pot` : (1 + 2f)^streets = 1 + 2·stack/pot.
pub fn geometric(pot: f64, stack: f64, streets: u32) -> f64 {
    ((1.0 + 2.0 * stack / pot).powf(1.0 / streets as f64) - 1.0) / 2.0
}

const STREETS: [char; 3] = ['f', 't', 'r'];

#[derive(Clone)]
struct State {
    street: u8,
    to_act: u8,
    put: [f64; 2],
    street_bet: [f64; 2],
    last_increment: f64,
    num_raises: u8,
    /// Dernier agresseur de la street précédente (None si elle a été checkée).
    last_aggressor: Option<u8>,
    checked: bool,
    /// Une lettre par street terminée (voir la clé des situations).
    past: String,
}

struct Builder<'a> {
    config: &'a TreeConfig,
    plan: &'a Plan,
    board_mask: u64,
    max_nodes: Option<usize>,
    nodes: Vec<Node>,
    children: Vec<u32>,
    actions: Vec<Action>,
    num_hands: [u64; 2],
    data_size: [u64; 2],
    tags: Vec<Tag>,
}

/// Construit l'arbre du plan. `config` : la configuration de GTOpen (pot, tapis, seuil de tapis,
/// relances maximum, tailles par défaut) ; `board_mask` : les cartes du board de départ.
pub fn build(
    config: &TreeConfig,
    plan: &Plan,
    root_past: &str,
    board_len: usize,
    board_mask: u64,
    num_hands: [usize; 2],
    max_nodes: Option<usize>,
) -> Result<(Tree, Vec<Tag>), String> {
    for key in plan.keys() {
        check_key(key)?;
    }
    let root_street = (board_len - 3) as u8;
    if root_past.len() != root_street as usize || !root_past.chars().all(|c| matches!(c, 'o' | 'i' | 'x')) {
        return Err(format!("passé {root_past:?} invalide pour un board de {board_len} cartes"));
    }
    let mut b = Builder {
        config,
        plan,
        board_mask,
        max_nodes,
        nodes: Vec::new(),
        children: Vec::new(),
        actions: Vec::new(),
        num_hands: [num_hands[0] as u64, num_hands[1] as u64],
        data_size: [0, 0],
        tags: Vec::new(),
    };
    let half = config.starting_pot / 2.0;
    b.action_node(State {
        street: root_street,
        to_act: OOP,
        put: [half, half],
        street_bet: [0.0, 0.0],
        last_increment: 0.0,
        num_raises: 0,
        last_aggressor: aggressor_of(root_past.chars().last()),
        checked: false,
        past: root_past.to_string(),
    })?;
    let tree = Tree {
        config: config.clone(),
        root_street,
        nodes: b.nodes,
        children: b.children,
        actions: b.actions,
        data_size: b.data_size,
    };
    Ok((tree, b.tags))
}

fn aggressor_of(letter: Option<char>) -> Option<u8> {
    match letter {
        Some('o') => Some(OOP),
        Some('i') => Some(IP),
        _ => None,
    }
}

fn check_key(key: &str) -> Result<(), String> {
    let parts: Vec<&str> = key.split(':').collect();
    let situation = |s: &str| {
        let c: Vec<char> = s.chars().collect();
        c.len() == 2 && STREETS.contains(&c[0]) && (c[1] == 'o' || c[1] == 'i')
    };
    let past = |s: &str| s.len() <= 2 && s.chars().all(|c| matches!(c, 'o' | 'i' | 'x'));
    let ok = match parts.as_slice() {
        ["bet", s, p] => situation(s) && past(p),
        ["raise", s, p, n] => situation(s) && past(p) && n.parse::<u8>().is_ok(),
        _ => false,
    };
    if ok {
        Ok(())
    } else {
        Err(format!("clé de plan invalide : {key:?}"))
    }
}

impl<'a> Builder<'a> {
    fn check_budget(&self) -> Result<(), String> {
        match self.max_nodes {
            Some(cap) if self.nodes.len() >= cap => Err(format!(
                "arbre trop grand : plus de {cap} nœuds ; réduis le nombre de tailles ou de relances"
            )),
            _ => Ok(()),
        }
    }

    fn key(&self, st: &State, raise: bool) -> String {
        let street = STREETS[st.street as usize];
        let who = if st.to_act == OOP { 'o' } else { 'i' };
        if raise {
            format!("raise:{street}{who}:{}:{}", st.past, st.num_raises)
        } else {
            format!("bet:{street}{who}:{}", st.past)
        }
    }

    /// Tailles proposées : celles du plan pour cette situation, sinon celles de la street.
    fn sizes(&self, st: &State, raise: bool, donking: bool) -> Vec<Size> {
        if let Some(sizes) = self.plan.get(&self.key(st, raise)) {
            return sizes.clone();
        }
        self.default_sizes(st, raise, donking)
    }

    fn default_sizes(&self, st: &State, raise: bool, donking: bool) -> Vec<Size> {
        let streets = if st.to_act == OOP { &self.config.oop } else { &self.config.ip };
        let sizing = &streets[st.street as usize];
        let list = if raise {
            &sizing.raise
        } else if donking {
            &sizing.donk
        } else {
            &sizing.bet
        };
        list.iter().map(Size::from).collect()
    }

    fn stack_of(&self, put: f64) -> f64 {
        self.config.effective_stack - (put - self.config.starting_pot / 2.0)
    }

    /// Actions du nœud et, pour chacune, les tailles (indices) qui y mènent.
    fn legal_actions(&self, st: &State) -> (Vec<Action>, Vec<Vec<usize>>) {
        let me = st.to_act as usize;
        let opp = 1 - me;
        let stack_me = self.stack_of(st.put[me]);
        let facing = st.street_bet[opp] - st.street_bet[me];
        let mut actions = Vec::new();
        let mut from: Vec<Vec<usize>> = Vec::new();
        if facing > 1e-9 {
            actions.push(Action::Fold);
            actions.push(Action::Call(st.street_bet[opp]));
            from.extend([Vec::new(), Vec::new()]);
            if stack_me > facing + 1e-9 && st.num_raises < self.config.max_raises {
                let pot_after_call = st.put[0] + st.put[1] + facing;
                let max_to = st.street_bet[me] + stack_me;
                let streets_left = 3 - st.street as u32;
                let mut wanted: Vec<(f64, Vec<usize>)> = self
                    .sizes(st, true, false)
                    .iter()
                    .enumerate()
                    .map(|(k, size)| (match *size {
                        Size::Pct(p) => st.street_bet[opp] + p / 100.0 * pot_after_call,
                        Size::Mult(m) => st.street_bet[opp] * m,
                        Size::Geo => {
                            st.street_bet[opp] + geometric(pot_after_call, stack_me - facing, streets_left) * pot_after_call
                        }
                        Size::AllIn => max_to,
                    }, vec![k]))
                    .collect();
                if self.config.add_allin {
                    wanted.push((max_to, Vec::new()));
                }
                let min_to = st.street_bet[opp] + st.last_increment.max(1e-9);
                let mut tos: Vec<(f64, Vec<usize>)> = Vec::new();
                for (mut to, ids) in wanted {
                    if to < min_to {
                        to = min_to;
                    }
                    if to >= max_to - 1e-9 || to >= self.config.allin_threshold * max_to - 1e-9 {
                        to = max_to;
                    }
                    if to > st.street_bet[opp] + 1e-9 {
                        tos.push((to, ids));
                    }
                }
                for (to, ids) in merge(tos) {
                    actions.push(Action::Raise(to));
                    from.push(ids);
                }
            }
        } else {
            actions.push(Action::Check);
            from.push(Vec::new());
            if stack_me > 1e-9 {
                // Donk : la BB mène dans l'agresseur de la street précédente (aussi à la racine d'un
                // sous-jeu, dont le passé est connu).
                let donking = st.to_act == OOP && st.last_aggressor == Some(IP);
                let sizes = self.sizes(st, false, donking);
                let pot = st.put[0] + st.put[1];
                let max_to = stack_me;
                let streets_left = 3 - st.street as u32;
                let mut wanted: Vec<(f64, Vec<usize>)> = sizes
                    .iter()
                    .enumerate()
                    .filter_map(|(k, size)| match *size {
                        Size::Pct(p) => Some((p / 100.0 * pot, vec![k])),
                        Size::Mult(_) => None,
                        Size::Geo => Some((geometric(pot, stack_me, streets_left) * pot, vec![k])),
                        Size::AllIn => Some((max_to, vec![k])),
                    })
                    .collect();
                if self.config.add_allin && (!sizes.is_empty() || !donking) {
                    wanted.push((max_to, Vec::new()));
                }
                let mut tos: Vec<(f64, Vec<usize>)> = Vec::new();
                for (mut to, ids) in wanted {
                    if to <= 1e-9 {
                        continue;
                    }
                    if to >= max_to - 1e-9 || to >= self.config.allin_threshold * max_to - 1e-9 {
                        to = max_to;
                    }
                    tos.push((to, ids));
                }
                for (to, ids) in merge(tos) {
                    actions.push(Action::Bet(to));
                    from.push(ids);
                }
            }
        }
        (actions, from)
    }

    /// Clé de la situation du nœud si elle est dans le plan (mise ou relance possible).
    fn planned(&self, st: &State) -> Option<(String, usize)> {
        let facing = st.street_bet[1 - st.to_act as usize] - st.street_bet[st.to_act as usize];
        let key = self.key(st, facing > 1e-9);
        self.plan.get(&key).map(|sizes| (key, sizes.len()))
    }

    fn rake(&self, pot: f64) -> f64 {
        let r = self.config.rake_pct * pot;
        if self.config.rake_cap > 0.0 {
            r.min(self.config.rake_cap)
        } else {
            r
        }
    }

    fn push(&mut self, node: Node) -> u32 {
        self.nodes.push(node);
        (self.nodes.len() - 1) as u32
    }

    fn node(kind: u8, player: u8, street: u8, put: [f64; 2]) -> Node {
        Node {
            kind,
            player,
            street,
            num_children: 0,
            children_start: 0,
            actions_start: 0,
            data_offset: 0,
            put,
            t_win: 0.0,
            t_lose: 0.0,
            t_tie: 0.0,
        }
    }

    fn action_node(&mut self, st: State) -> Result<u32, String> {
        self.check_budget()?;
        let (actions, from) = self.legal_actions(&st);
        let n = actions.len();
        if n == 0 || n > 250 {
            return Err(format!("nombre d'actions invalide ({n}) pendant la construction de l'arbre"));
        }
        let actions_start = self.actions.len() as u32;
        self.actions.extend(actions.iter().copied());
        let me = st.to_act as usize;
        let data_offset = self.data_size[me];
        self.data_size[me] += n as u64 * self.num_hands[me];
        // Le nœud passe avant ses enfants, comme dans GTOpen.
        let idx = self.push(Node {
            num_children: n as u8,
            actions_start,
            data_offset,
            ..Self::node(KIND_ACTION, st.to_act, st.street, st.put)
        });
        if let Some((key, sizes)) = self.planned(&st) {
            if from.iter().any(|ids| !ids.is_empty()) {
                self.tags.push(Tag { node: idx, key, sizes, actions: from });
            }
        }
        let mut kids = Vec::with_capacity(n);
        for action in actions {
            kids.push(self.apply(&st, action)?);
        }
        self.nodes[idx as usize].children_start = self.children.len() as u32;
        self.children.extend(kids);
        Ok(idx)
    }

    fn apply(&mut self, st: &State, action: Action) -> Result<u32, String> {
        let me = st.to_act as usize;
        let opp = 1 - me;
        match action {
            Action::Fold => {
                let f = st.put[me];
                let rake = self.rake(2.0 * f);
                Ok(self.push(Node {
                    t_win: f - rake,
                    t_lose: -f,
                    ..Self::node(KIND_TERM_FOLD, st.to_act, st.street, st.put)
                }))
            }
            Action::Check if st.checked => self.street_end(st, st.put, None, false),
            Action::Check => self.action_node(State { to_act: st.to_act ^ 1, checked: true, ..st.clone() }),
            Action::Call(to) => {
                let mut put = st.put;
                put[me] += to - st.street_bet[me];
                let allin = self.stack_of(put[me]) <= 1e-9 || self.stack_of(put[opp]) <= 1e-9;
                self.street_end(st, put, Some(st.to_act ^ 1), allin)
            }
            Action::Bet(to) | Action::Raise(to) => {
                let mut put = st.put;
                put[me] += to - st.street_bet[me];
                let mut street_bet = st.street_bet;
                street_bet[me] = to;
                let increment = to - st.street_bet[opp].max(st.street_bet[me]);
                self.action_node(State {
                    to_act: st.to_act ^ 1,
                    put,
                    street_bet,
                    last_increment: increment.max(st.last_increment),
                    num_raises: st.num_raises + matches!(action, Action::Raise(_)) as u8,
                    ..st.clone()
                })
            }
        }
    }

    /// Fin de street : abattage, déroulé sans action (tapis) ou carte suivante puis nouvelle street.
    /// `aggressor` : dernier agresseur de la street (None si elle a été checkée).
    fn street_end(&mut self, st: &State, put: [f64; 2], aggressor: Option<u8>, allin: bool) -> Result<u32, String> {
        if st.street == 2 {
            return Ok(self.showdown(2, put));
        }
        if allin {
            return self.runout(st.street, put);
        }
        self.check_budget()?;
        let next = st.street + 1;
        let idx = self.push(Self::node(KIND_CHANCE, 0, next, put));
        let past = format!("{}{}", st.past, letter_of(aggressor));
        let mut kids = vec![SENTINEL; 52];
        for card in 0..52u8 {
            if self.board_mask & (1 << card) != 0 {
                continue;
            }
            kids[card as usize] = self.action_node(State {
                street: next,
                to_act: OOP,
                put,
                street_bet: [0.0, 0.0],
                last_increment: 0.0,
                num_raises: 0,
                last_aggressor: aggressor,
                checked: false,
                past: past.clone(),
            })?;
        }
        self.nodes[idx as usize].children_start = self.children.len() as u32;
        self.nodes[idx as usize].num_children = 52;
        self.children.extend(kids);
        Ok(idx)
    }

    fn runout(&mut self, street: u8, put: [f64; 2]) -> Result<u32, String> {
        self.check_budget()?;
        let next = street + 1;
        let idx = self.push(Self::node(KIND_CHANCE, 0, next, put));
        let mut kids = vec![SENTINEL; 52];
        for card in 0..52u8 {
            if self.board_mask & (1 << card) != 0 {
                continue;
            }
            kids[card as usize] = if next == 2 { self.showdown(2, put) } else { self.runout(next, put)? };
        }
        self.nodes[idx as usize].children_start = self.children.len() as u32;
        self.nodes[idx as usize].num_children = 52;
        self.children.extend(kids);
        Ok(idx)
    }

    fn showdown(&mut self, street: u8, put: [f64; 2]) -> u32 {
        let rake = self.rake(put[0] + put[1]);
        self.push(Node {
            t_win: put[0] - rake,
            t_lose: -put[0],
            t_tie: -rake / 2.0,
            ..Self::node(KIND_TERM_SHOWDOWN, 0, street, put)
        })
    }
}

fn letter_of(aggressor: Option<u8>) -> char {
    match aggressor {
        Some(p) if p == OOP => 'o',
        Some(_) => 'i',
        None => 'x',
    }
}

/// Montants triés, ceux à moins de 1e-6 confondus (le premier reste, comme dans GTOpen), avec les
/// tailles qui y mènent.
fn merge(mut v: Vec<(f64, Vec<usize>)>) -> Vec<(f64, Vec<usize>)> {
    v.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut out: Vec<(f64, Vec<usize>)> = Vec::new();
    for (x, ids) in v {
        match out.last_mut() {
            Some(last) if (x - last.0).abs() < 1e-6 => last.1.extend(ids),
            _ => out.push((x, ids)),
        }
    }
    out
}

/// Les deux arbres sont-ils identiques (nœuds, enfants, actions, tailles des données) ? Sinon, où
/// est la première différence.
pub fn same_tree(a: &Tree, b: &Tree) -> Result<(), String> {
    if a.nodes.len() != b.nodes.len() {
        return Err(format!("{} nœuds contre {}", a.nodes.len(), b.nodes.len()));
    }
    for (i, (x, y)) in a.nodes.iter().zip(&b.nodes).enumerate() {
        let same = x.kind == y.kind
            && x.player == y.player
            && x.street == y.street
            && x.num_children == y.num_children
            && x.children_start == y.children_start
            && x.actions_start == y.actions_start
            && x.data_offset == y.data_offset
            && x.put == y.put
            && x.t_win == y.t_win
            && x.t_lose == y.t_lose
            && x.t_tie == y.t_tie;
        if !same {
            return Err(format!("nœud {i} différent : {x:?} / {y:?}"));
        }
    }
    if a.children != b.children {
        return Err("enfants différents".into());
    }
    if a.actions != b.actions {
        return Err("actions différentes".into());
    }
    if a.data_size != b.data_size || a.root_street != b.root_street {
        return Err("tailles des données différentes".into());
    }
    Ok(())
}
