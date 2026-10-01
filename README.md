# Analyzer

Outil d'analyse de tes adversaires en **Heads-Up NLHE** à partir de tes historiques de mains.
Il lit les historiques, calcule le profil complet de l'adversaire (et le tien dans le même
match), détecte ses tendances exploitables et génère un **rapport HTML** autonome, un
**visualiseur de spots** pour filtrer et rejouer les coups toi-même, une comparaison de tes
décisions préflop à une **solution de solveur** HU, et la **résolution postflop** d'un coup avec le
moteur de [GTOpen](https://github.com/MatthewPDingle/GTOpen).

- Python 3.10+, **aucune dépendance** à installer (Pillow seulement pour lire de nouvelles captures de ranges ;
  Rust et git seulement pour installer le solveur postflop).
- Sites supportés : **Betclic.fr** (cash game HU). D'autres formats peuvent être ajoutés (voir plus bas).

## Application

```bash
python -m analyzer app
```

L'application s'ouvre dans ton navigateur (http://127.0.0.1:8765). Tout reste sur ton ordinateur :
le serveur n'écoute qu'en local et refuse les requêtes venant d'autres sites.

- **Menu latéral** : « Mon jeu », « Importer des mains » et la liste de tes adversaires (recherche,
  nombre de mains, ton résultat contre chacun).
- **Adversaire** : onglets *Plan de jeu*, *Préflop* (tes décisions et ses fréquences face au solveur),
  *Rapport* et *Spots*. Les liens « voir les mains » et « rejouer » ouvrent directement l'onglet Spots
  sur la bonne ligne ou la bonne main. Dans le replayer, **Résoudre ce coup** lance le solveur GTOpen
  (voir plus bas).
- **Mon jeu** : ton bilan contre tous tes adversaires (résultats, courbe, écarts aux repères, stats,
  pertes sans abattage, résultats par adversaire), *Mon préflop* face au solveur sur toutes tes mains,
  et *Mes spots*.
- **Importer des mains** : glisse tes historiques ou choisis-les ; ils sont copiés dans le dossier des
  mains (`hands/` par défaut), les doublons et les formats non reconnus sont signalés.

Options : `--dossier` (dossier des historiques), `--port`, `--hero`, `--sans-navigateur`.
Les analyses sont calculées à la première ouverture d'une page puis gardées en mémoire ; un import les recalcule.

## Ligne de commande

1. Dépose tes fichiers d'historique dans `hands/` (un ou plusieurs fichiers `.txt`, sous-dossiers acceptés).
   Les mains en double sont ignorées : tu peux accumuler les sessions contre un même joueur.
2. Lance :

```bash
python -m analyzer                       # un rapport par adversaire (30 mains minimum)
python -m analyzer --liste               # liste les adversaires trouvés
python -m analyzer -a berserk            # un seul adversaire (nom partiel, accents ignorés)
python -m analyzer -a berserk --plan     # seulement le plan de jeu, dans le terminal
python -m analyzer ~/Downloads/Hand.txt -a "peste noire" -o rapports/
python -m analyzer -a berserk --sans-spots   # rapport seul, plus rapide
```

Ton pseudo est détecté automatiquement (tag `Hero` de Betclic) ; sinon passe `--hero TonPseudo`.
Le terminal affiche un résumé ; le rapport complet est écrit dans `reports/<adversaire>.html`, la page
préflop dans `reports/<adversaire>-preflop.html` et le visualiseur dans `reports/<adversaire>-spots.html`
(ouvre-les dans ton navigateur, ils fonctionnent hors ligne).

## Comment ça marche

1. **Lecture** (`parsers/`) : chaque historique est découpé en mains, converties dans un format commun
   (positions, tapis, actions avec montants, board, cartes montrées, gains, rake). Chaque pot est vérifié :
   mises − montants non payés = pot total, et gains + rake = pot total.
2. **Lecture des situations** (`stats.py`) : pour chaque main, chaque décision est rangée dans sa situation
   (« BB face à un open », « agresseur qui peut c-bet le flop », « face à un check-raise »…). Une stat vaut
   toujours « fois où il l'a fait / fois où il pouvait le faire ». Les deux joueurs sont analysés de la même façon.
3. **Lecture de l'adversaire** (`insights.py`) : ses stats sont comparées à des repères (ceux du préflop
   viennent de la solution du solveur, voir plus bas) ; un écart est « net » si l'intervalle de confiance
   à 90 % reste hors du repère, sinon « tendance ».
4. **Ses lignes** (`lines.py`) : chaque mise postflop est rangée par ligne (contexte et taille). À l'abattage,
   sa main est classée au moment de la mise (value, value fine, semi-bluff, bluff) grâce à un évaluateur
   de mains et à un calcul d'équité exact contre ta main. Ta main quand tu foldes est aussi notée.
5. **Préflop vs solveur** (`theory/`) : chaque décision préflop dont on connaît les cartes est rangée
   dans l'arbre de la solution et comparée à ce que le solveur fait avec cette main.
6. **Plan de jeu** (`plan.py`) : des règles génériques transforment ces constats en consignes, chacune
   avec sa preuve et un niveau de confiance.
7. **Sorties** (`report.py`, `viewer.py`, `selfreport.py`) : le rapport HTML, le visualiseur de spots,
   le bilan « Mon jeu » et le résumé du terminal.
8. **Application** (`app/`) : un serveur local sert ces pages dans une interface avec menu et onglets,
   calcule chaque analyse à la demande et la garde en cache jusqu'au prochain import.

## Visualiseur de spots

`reports/<adversaire>-spots.html` liste toutes les mains du match et permet de les filtrer :

- **spot** : type de pot (SRP, 3bet, 4bet+, limpé), agresseur préflop, ta position, street atteinte ;
- **actions** sur une street : les tiennes et les siennes (mise, relance, call, fold, check-call, check-raise…),
  c-bet de l'agresseur (occasion, fait, checké) ;
- **ligne** : les mêmes lignes que dans la section « Ses lignes » du rapport (et les tiennes) ;
- **fin du coup** (abattage, tu folds à telle street, il folde, all-in), sa main connue ou non, résultat,
  recherche par main (`AKo`, `99`, `Ah`) ou par numéro de main.

Des spots prédéfinis sont proposés (c-bets flop en SRP, pots 3bet au flop en BB ou au bouton, ses
check-raises, ses barrels turn, tes folds river, all-in, grosses pertes). Pour chaque sélection, le
visualiseur affiche ton résultat et la fréquence de chaque action sur la street choisie.

Clique sur une main pour la **rejouer** action par action (flèches ← → du clavier, Début/Fin) : tapis, pot,
mises, board, temps de réflexion, ta main à chaque street et, si sa main a été montrée, la sienne et ton
équité. Sa main reste cachée jusqu'à l'abattage (case « Montrer sa main » pour la voir avant).

Depuis le rapport, chaque ligne de « Ses lignes » a un lien « voir les mains » et chaque main de la
section abattage un lien « rejouer ». Les filtres sont gardés dans l'adresse de la page : tu peux garder
un spot en favori.

## Préflop vs solveur

La solution de référence est dans `analyzer/theory/data/hu_100bb.json` : quatre nœuds (bouton premier à
parler, BB face à l'open, bouton face au 3bet, BB face au 4bet), avec pour chacune des 169 mains la
fréquence de chaque action. Elle a été lue sur les captures de ranges HU à 100bb (open 2,5bb, 3bet 11,5bb,
4bet 26bb, puis tapis).

Pour chaque décision (tes cartes sont toujours connues ; les siennes seulement à l'abattage) :

- **principale** : l'action que le solveur prend au moins 50 % du temps avec cette main ;
- **secondaire** : il la prend parfois (10 à 50 %) ;
- **écart** : il la prend moins de 10 % du temps ;
- **hors range** : le solveur n'amène jamais cette main à ce nœud (ex. un 4bet face à un 3bet avec une main
  qu'il n'ouvre pas) ; ces décisions sont exclues des fréquences.

La page montre, par nœud, la grille du solveur (couleurs = actions, hauteur = part de la main qui arrive
ici) avec tes décisions par main (✕ = écart), tes fréquences face à celles du solveur **avec exactement les
mêmes mains**, et la liste des écarts avec un lien « rejouer ». Pour l'adversaire : ses fréquences globales
face au solveur et les mains montrées qu'il joue autrement.

Limites : la lecture des captures est précise à environ 2 % ; la solution est à 100bb et avec ces tailles,
alors que ta profondeur et vos tailles réelles peuvent différer (la page les affiche). Les pots limpés et
les relances après un limp sont hors de l'arbre.

Pour remplacer ou ajouter un nœud à partir d'une capture de grille 13 × 13 (nécessite `pip install pillow`) :

```bash
python -m analyzer.theory.extract capture.png                     # affiche les fréquences lues
python -m analyzer.theory.extract capture.png --solution analyzer/theory/data/hu_100bb.json --noeud bb_vs_open
```

## Résoudre un coup avec GTOpen (postflop)

Le moteur du solveur open source [GTOpen](https://github.com/MatthewPDingle/GTOpen), de Matthew Dingle,
résout le coup à partir du flop puis compare chaque décision de la main à la stratégie d'équilibre.
Son code n'est pas inclus dans ce dépôt : l'installation le récupère depuis son dépôt d'origine.

**Installation** (une fois) : installe [Rust](https://rustup.rs) et git, puis

```bash
python -m analyzer gtopen --installer                 # récupère GTOpen et compile le solveur
python -m analyzer gtopen --installer --source ~/GTOpen   # si tu as déjà une copie de GTOpen
python -m analyzer gtopen                             # état du solveur
```

L'installation fait une copie partielle de GTOpen dans `~/.analyzer/GTOpen` : le moteur et les quelques
fichiers qu'il lit à la compilation, sans ses données de recherche, à la version testée avec Analyzer
(`GTOPEN_COMMIT` dans `postflop.py`). Relancer `--installer` complète ou remet à niveau une copie
existante. Elle compile ensuite `analyzer-solve`, un petit programme d'Analyzer
(`analyzer/theory/native/main.rs`) qui utilise ce moteur : il résout le spot et suit la ligne jouée.
`--gpu` compile aussi le moteur CUDA de GTOpen pour une carte NVIDIA (expérimental, voir le README de
GTOpen pour les bibliothèques CUDA nécessaires).

**Utilisation** : dans l'application, ouvre un coup dans *Spots* et clique sur **Résoudre ce coup**. Le calcul
tourne en arrière-plan (une résolution à la fois) ; tu peux continuer à naviguer et revenir plus tard. En
ligne de commande : `python -m analyzer gtopen -m <numéro de main>` (options `--precision`, `--iterations`,
`--threads`). Chaque résolution est enregistrée dans `~/.analyzer/resolutions` : un coup déjà résolu
s'affiche tout de suite.

**Le spot** construit pour une main :

- ranges de départ tirées de la solution préflop : SRP (open du bouton / call de la BB), pot 3bet
  (3bet de la BB / call du bouton), pot 4bet (4bet du bouton / call de la BB) ;
- board, pot et tapis effectif au flop, en bb, sans rake ;
- tailles : mise 33 % au flop, 75 % à la turn et à la river, relance 60 % du pot au flop et à la turn,
  deux relances au plus par street, pas de donk (mise d'ouverture hors de position après avoir payé) :
  en SRP et en pot 4bet, la BB ne mène ni au flop ni après une mise du bouton ; elle mise à la turn après
  un flop checké. En pot 3bet, la BB a l'initiative et c-bette normalement.
  Les tailles réellement jouées dans la main sont ajoutées (ou remplacent la taille par défaut la plus
  proche) pour que chaque décision tombe sur une branche de l'arbre, y compris un donk joué.

**La lecture**, pour chaque décision : la stratégie du solveur avec ta main exacte, avec toute ta range,
l'EV de chaque action et la perte d'EV de ton choix ; le même verdict qu'au préflop (action principale,
secondaire ou écart) ; une grille 13 × 13 de la range de celui qui agit. Sa main n'apparaît que si le
replayer la dévoile.

**L'explorateur** (*Ouvrir l'explorateur ↗* dans le panneau du solveur, ou `/explorateur/<numéro de main>`)
s'ouvre dans une nouvelle fenêtre, comme un solveur :

- en haut, le déroulé du coup : chaque nœud avec ses actions (● = action jouée dans la main) ; clique sur
  une action pour suivre une autre branche, sur la turn ou la river pour changer de carte, ← pour revenir ;
- la grille 13 × 13 de la range choisie (celle du joueur qui agit, ou l'autre), en *Stratégie*,
  *Stratégie + EV*, *EV* ou *Équité* ; la hauteur d'une case est la part de la main encore présente ;
- à droite, la fréquence de chaque action pour toute la range (et le nombre de combos), ta main et,
  quand le coup est dévoilé, la sienne ;
- au survol d'une case, le détail de chacun de ses combos : fréquence et/ou EV de chaque action selon
  l'affichage choisi, équité, présence ; un clic garde la case affichée ;
- l'onglet *Filtres* : la part de la range et la stratégie de chaque catégorie de mains (mains faites, de la
  quinte flush aux mains non faites ; tirages au flop et à la turn ; équité, en 4 ou 7 tranches ; couleurs
  dépareillées ou assorties). Un clic sur une ou plusieurs lignes ne garde (ou n'écarte) que ces mains dans
  la grille, la synthèse et le détail des combos ; le filtre reste actif d'un nœud à l'autre ;
- chaque case du déroulé affiche le pot à ce moment ; un clic sur une case ramène à ce moment du coup.

Les mises sont en % du pot ; les relances aussi, selon la convention des solveurs : le montant ajouté
rapporté au pot après le call (relancer à 4,5 sur une mise de 1,7 dans un pot de 5 = 2,8 / 8,4 = 33 %).

**L'EV** est en bb, à partir du moment du coup affiché : un fold vaut 0, le pot déjà au milieu est à gagner
et les mises à venir sont dépensées. Pour une action (au survol), c'est ce que rapporte cette action puis la
suite jouée par le solveur ; pour une main (dans la grille), c'est l'EV de sa stratégie, la moyenne de ses
actions pondérée par leurs fréquences. Une main qui folde 100 % vaut donc 0 même si payer coûterait 14 bb.

**Études du solveur** : chaque coup résolu est gardé sur disque (`~/.analyzer/etudes`, environ 170 Mo pour
un pot simplement relancé) sous une forme compacte : la stratégie de chaque nœud sur 8 bits, compressée.
L'explorateur rouvre une étude en une quinzaine de secondes au lieu de la recalculer (les fréquences
restent à 1 point près, les EV à quelques centièmes de bb). La page *Études du solveur* de l'application
liste les études, avec leur précision et leur taille, et permet de les rouvrir ou de les supprimer.
Une étude ouverte occupe environ 2 Go de mémoire : une seule reste ouverte, fermée après 30 minutes sans
activité ou quand une autre s'ouvre.

**Durée** : un arbre de flop compte environ 700 000 nœuds et 2 Go de mémoire avec les ranges HU complètes.
Sur un processeur à 4 cœurs, l'objectif par défaut (1,5 % du pot d'exploitabilité, 120 itérations au plus)
demande environ 5 minutes ; c'est plus rapide avec plus de cœurs.

**Spots d'étude** : des spots résolus sans main jouée, pour travailler une situation type. La première
série est le **SRP** (open du bouton à 2,5 bb, call de la BB, 100 bb, mêmes ranges et même arbre que
ci-dessus) sur 24 flops : trois par texture (un sec, un connecté, un deux couleurs), dans cet ordre de
classement :

| Texture | Flops |
| --- | --- |
| Pairé | K♠K♦4♣ · 8♥8♣5♠ · J♦3♣3♥ |
| Monotone | A♠8♠3♠ · J♥9♥5♥ · 7♦5♦2♦ |
| Ace high | A♠7♥2♦ · A♣K♦9♥ · A♥6♥4♣ |
| King high | K♠8♦3♥ · K♥Q♣9♦ · K♦7♦5♣ |
| Queen high | Q♠7♦2♥ · Q♥J♣9♦ · Q♣8♣4♦ |
| Jack high | J♠6♦3♥ · J♥T♣8♦ · J♣9♣4♦ |
| Ten high | T♠5♦2♥ · T♥9♣7♦ · T♣8♣3♦ |
| Low board (9 et moins) | 9♠5♦2♥ · 8♥7♣5♦ · 6♣4♣2♦ |

Un flop pairé ou monotone est classé comme tel ; sinon par sa plus haute carte. Pour les résoudre :
le bouton **Résoudre les flops manquants** de la page *Études du solveur* (en arrière-plan, l'un après
l'autre, avec une barre de progression ; *Arrêter* interrompt la série), ou en ligne de commande :

```bash
python -m analyzer gtopen --spots srp                          # les 24 flops (ceux déjà résolus sont passés)
python -m analyzer gtopen --spots srp --texture Monotone --texture "Ace high"   # quelques textures
```

La série alterne les textures (un flop de chaque, puis un deuxième…) : interrompue, elle couvre déjà
toutes les textures, et une relance reprend où elle s'était arrêtée. Compter 1 à 5 minutes par flop sur
4 cœurs et 70 à 170 Mo sur le disque. La page *Études du solveur* montre ensuite, texture par texture
(avec la moyenne de ses flops), la stratégie de toute la range : la c-bet du bouton après le check de la
BB, la réponse de la BB (fold, call, check-raise), puis celle du bouton face au check-raise. *Explorer ↗*
ouvre le spot dans l'explorateur (`/explorateur/spot:srp:KsKd4c`), turn et river comprises : l'étude se
rouvre d'elle-même, le choix de la carte s'ouvre quand on arrive à la turn ou à la river, et les checks
forcés de la BB (qui ne mène pas) sont passés pour aller droit à la décision suivante.
*Étudier un autre flop* ouvre n'importe quel flop dans l'explorateur ; une fois résolu, il rejoint sa
texture dans la page.

**Limites** : les tailles et la profondeur de l'arbre simplifient le jeu réel ; une main que la range du
solveur ne contient pas (par exemple un open que le solveur ne fait jamais) y est ajoutée avec un poids
infime pour lire sa stratégie, à prendre avec prudence ; les pots limpés et les 5bets ne sont pas couverts.

Variables d'environnement : `ANALYZER_HOME` (dossier de travail, `~/.analyzer` par défaut),
`GTOPEN_DIR` (copie de GTOpen à utiliser), `ANALYZER_SOLVER` (chemin d'un `analyzer-solve` déjà compilé).

## Contenu du rapport

| Section | Ce qu'on y trouve |
|---|---|
| Plan de jeu | Son profil en une phrase et au plus 4 consignes par moment du coup (préflop, quand tu mises, face à ses mises, à tester), chacune avec sa preuve chiffrée et un niveau de confiance |
| Résultat | Ton gain en bb et en €, bb/100, résultat **EV all-in** (la part de chance), gains avec/sans abattage, courbe main par main |
| Lecture de l'adversaire | Ses écarts aux repères d'un régulier HU, classés par importance, avec l'exploit correspondant ; tes propres écarts dans ce match |
| Le duel | Ses attaques face à tes réponses (ses 3bets / tes folds vs 3bet, ses barrels / tes folds...), avec alerte quand il t'exploite — en tenant compte de ta main quand tu as foldé |
| Ses lignes : value ou bluff ? | Chaque ligne postflop (c-bet, check-raise, barrel, mise après check…, par taille) : combien de fois, ta réponse, **avec quoi tu as foldé**, ce qu'il a montré (bluff / semi-bluff / value fine / value, selon son équité réelle contre ta main) et un verdict qui tient compte de la taille de l'échantillon. Synthèse par taille, ce qu'il montre quand il checke, timing selon sa main |
| Où partent tes bb sans abattage | Pertes par street quand tu folds ou quand il folde, tes folds turn/river les plus coûteux par déroulé, et ce que tu as quand tu checkes |
| Tes bluffs | Sa fréquence de fold face à tes mises, par taille, comparée au seuil de rentabilité d'un bluff pur |
| Statistiques | Préflop au bouton et en BB, postflop agresseur / défenseur, check-raise, abattage, agression par street — toujours en `x/n` |
| Sizings | Ses tailles de relance préflop, la répartition de ses mises postflop par tranche de % du pot, et **ce qu'il avait à l'abattage** pour chaque tranche |
| Timing | Son temps de décision médian par type d'action |
| All-in | Chaque all-in avec ton équité, ton EV et le résultat réel |
| Mains à l'abattage | Toutes ses mains montrées, regroupées par ligne préflop, avec le déroulé complet et son temps de réflexion |

### Définitions

- **Situation / fréquence** : chaque stat est « nombre de fois où il l'a fait / nombre de fois où il pouvait le faire ».
- **Open-raise, limp, fold d'entrée** : première action au bouton. **3bet vs open** : relance de la BB face à un open.
- **C-bet** : mise de l'agresseur préflop quand l'action lui revient sans mise devant lui. **Barrel turn / river** : c-bet à la street suivante après avoir c-bet la précédente.
- **C-bet retardé** : l'agresseur checke le flop (flop checké des deux côtés) puis mise le turn.
- **Probe turn** : la BB mise le turn après que l'agresseur a checké derrière au flop.
- **Bet si l'agresseur checke** : le défenseur en position mise quand l'agresseur checke le flop.
- **WTSD** : va à l'abattage quand il voit le flop. **W$SD** : gagne à l'abattage. **AF** = (bets + raises) / calls, **AFq** = (bets + raises) / (bets + raises + calls + folds).
- **Repères** : au préflop, la fréquence globale du solveur (± 5 points, ± 3 sous 15 %) ; au postflop, des
  ordres de grandeur pour un régulier HU solide à 100bb+. Ils servent à repérer les écarts, pas à définir une stratégie optimale. Un écart est marqué **net** quand l'intervalle de confiance à 90 % est entièrement hors du repère, sinon **tendance**. Il faut au moins 15 occasions pour qu'une stat soit interprétée.
- **Mains montrées** : seuls les coups allés à l'abattage sont visibles ; ses bluffs qui t'ont fait folder n'apparaissent jamais.
- **Intention d'une mise** (section « Ses lignes ») : sa main au moment de la mise. *Value* = top paire ou mieux, *value fine* = paire faible ou moyenne, *semi-bluff* = rien de fait mais au moins 25 % d'équité contre ta main (flop, turn), *bluff* = le reste. À la river, ta décision de payer ne dépend pas de ses cartes : les mains vues quand tu paies sont un échantillon honnête de la ligne. Le verdict « payer / folder tes bluff-catchers » compare sa part de bluffs (intervalle de confiance à 90 %) aux cotes du pot, et reste « pas encore tranché » tant que l'échantillon ne permet pas de conclure.
- **Folds forcés** : un fold sans paire ni tirage n'est pas une erreur. Le rapport indique toujours avec quoi tu as foldé avant de parler de sur-fold.

## Structure

```
analyzer/
  parsers/betclic.py   lecture des historiques Betclic
  models.py            modèle commun (Hand, Action, Seat)
  stats.py             lecture des situations HU et agrégation des stats
  cards.py             notation des mains, évaluateur 7 cartes, équité
  insights.py          repères, exploits, duel, tells de sizing
  lines.py             lignes value / bluff, folds forcés, pertes sans abattage
  plan.py              plan de jeu généré à partir de l'analyse
  spots.py             fiches des mains pour le visualiseur (tags de spot, lignes, équités)
  report.py            rapport HTML
  viewer.py            visualiseur de spots (HTML + JavaScript, sans dépendance)
  selfreport.py        « Mon jeu » : ton bilan contre tous tes adversaires
  theory/              préflop vs solveur : solution (data/), comparaison (preflop.py), page (page.py),
                       catégories de mains pour les filtres (handclass.py),
                       lecture de captures de ranges (extract.py) ; postflop avec GTOpen : spots,
                       installation et lecture des résultats (postflop.py), pont Rust (native/main.rs),
                       commande `gtopen` (solve_cli.py)
  app/                 application : serveur local (server.py), bibliothèque de mains et cache
                       (library.py), résolutions et sessions du solveur (solves.py), interface
                       (static/, dont l'explorateur explorer.*)
  cli.py               ligne de commande
tests/                 tests unitaires (python -m unittest)
hands/                 tes historiques (ignorés par git)
reports/               rapports générés (ignorés par git)
```

### Ajouter un site

Crée `analyzer/parsers/<site>.py` avec deux fonctions, `looks_like(text) -> bool` et
`parse(text) -> Iterator[Hand]`, puis ajoute le module à `PARSERS` dans
`analyzer/parsers/__init__.py`. Le reste (stats, rapport) fonctionne sans modification.
Les montants d'une `Action` sont des incréments (`amount`) et le total engagé sur la street (`to`).

## Tests

```bash
python -m unittest discover -s tests
ANALYZER_TEST_GTOPEN=1 python -m unittest tests.test_postflop   # + une vraie résolution (solveur installé)
```

Les autres tests du postflop utilisent un faux solveur (`tests/fixtures/fake_solver.py`) : ils ne
demandent ni Rust ni GTOpen.

## Plan de jeu

Le plan est produit par des règles génériques : chacune ne se déclenche que si les données la justifient
(par exemple « face à ses 3bets, défends plus » exige que tu foldes au moins 5 points de plus que le solveur
avec les mêmes mains). Au préflop, les écarts au solveur répétés au moins 4 fois deviennent des consignes
(« jette ces mains », « 3bet ces mains au lieu de payer »…), sauf quand l'écart exploite une de ses fuites
(ouvrir large contre un joueur qui abandonne trop sa BB, par exemple).
Chaque consigne affiche sa preuve et un niveau de confiance :

- **solide** : l'écart reste vrai même en tenant compte du hasard (intervalle de confiance à 90 %) ;
- **indicatif** : tendance nette sur un échantillon modeste ;
- **à confirmer** : peu de mains, à vérifier sur les prochaines sessions.

La rubrique « À tester » liste ses lignes turn et river qu'on n'a presque jamais vues à l'abattage alors que
tu as souvent foldé une paire ou mieux : c'est là qu'un call de temps en temps apporte l'information qui manque.
Plus tu accumules de sessions contre un joueur dans `hands/`, plus le plan s'affine.
