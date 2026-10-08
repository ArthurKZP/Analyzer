# Analyzer

Outil d'analyse de tes adversaires en **Heads-Up NLHE** à partir de tes historiques de mains.
Il lit les historiques, calcule le profil complet de l'adversaire (et le tien dans le même
match), détecte ses tendances exploitables et génère un **rapport HTML** autonome, un
**visualiseur de spots** pour filtrer et rejouer les coups toi-même, une comparaison de tes
décisions préflop à une **solution de solveur** HU, et la **résolution postflop** d'un coup avec le
moteur de [GTOpen](https://github.com/MatthewPDingle/GTOpen).

- Python 3.10+, **aucune dépendance** à installer (Pillow seulement pour lire de nouvelles captures de ranges ;
  Rust et git seulement pour installer le solveur postflop).
- Sites supportés : **Betclic.fr**, **Winamax** et **Unibet** (cash game NLHE). Les mains **heads-up** ont toute
  l'analyse ; celles des **tables de 3 à 9 joueurs**, réunies sous *Tables à plusieurs*, ont leurs stats par
  position, leur Leakfinding, leur préflop face aux charts et leurs pots à deux au solveur (voir *Sites et formats
  de table*). D'autres sites peuvent être ajoutés (voir plus bas).

## Application

```bash
python -m analyzer app
```

L'application s'ouvre dans ton navigateur (http://127.0.0.1:8765). Tout reste sur ton ordinateur :
le serveur n'écoute qu'en local et refuse les requêtes venant d'autres sites.

- **Menu latéral** : « Mon jeu », « Étude du field », « Études du solveur », « Importer des mains » et la liste de
  tes adversaires heads-up (recherche sans tenir compte des accents, tri par mains jouées, meilleur ou pire résultat,
  plus récents ou nom, filtre réguliers / récréatifs ; le tri et le filtre sont gardés d'une visite à l'autre), avec
  le nombre de mains et ton résultat contre chacun.
- **Adversaire** : onglets *Plan de jeu*, *Préflop* (tes décisions et ses fréquences face au solveur),
  *Rapport*, *Spots* et *Face au solveur* (tes mains postflop contre lui comparées au solveur, et ses écarts
  à exploiter). En haut de la fiche, son type : régulier ou récréatif (voir « Face au solveur »). Les liens « voir les mains » et « rejouer » ouvrent directement l'onglet Spots
  sur la bonne ligne ou la bonne main. Dans le replayer, **Résoudre ce coup** lance le solveur GTOpen
  (voir plus bas).
- **Mon jeu** : ton *Bilan* contre tous tes adversaires — en tête, tes résultats **tous formats confondus**, en
  heads-up et aux tables à plusieurs ; puis, au choix *Heads-up* / *Tables à plusieurs* (retenu avec le Leakfinding
  et Mon préflop), le même plan : résultat et courbe (réel, EV all-in, avec et sans abattage), **tes écarts les plus
  importants** avant le détail de tes stats (aux tables à plusieurs, sur toutes tes mains, contre les réguliers et
  contre les récréatifs), résultats par adversaire avec recherche, filtre par type et tri par mains, résultat,
  bb/100, date ou nom (aux tables à plusieurs : mains ensemble, pots disputés et ton résultat dans ces pots), pertes
  sans abattage (heads-up) ; *Leakfinding*, *Mains de départ* (ce que rapporte chaque main, voir
  plus bas), *Mon préflop*, *Mes spots*, *Face au solveur* (tes erreurs postflop, voir plus bas) et *Tables à
  plusieurs*. *Mon préflop* compare tes décisions préflop à la solution heads-up, ou, aux tables à plusieurs (choix
  *Heads-up* / *Tables à plusieurs* en haut de la page, retenu avec celui du Leakfinding), à tes charts avec les mêmes
  cartes : un nœud par situation et positions (premier à parler, face à l'open de telle position, face au 3bet après
  ton open), avec la grille de la stratégie, tes fréquences face aux charts et tes écarts main par main ; une table
  sans charts à elle prend ceux du 6-max à même nombre de joueurs derrière, et les mains contre les récréatifs sont
  exclues.
- **Étude du field** : comment jouent tes adversaires. *Bluffs des réguliers* (où ils bluffent, ensemble puis un par
  un, voir plus bas) et *Les joueurs* : tes adversaires en heads-up et aux tables à plusieurs, réguliers et
  récréatifs, avec recherche, tri et leur type réglable (le même partout).
- **Entraîneur** : joue des mains sur les spots résolus, le solveur juge chaque décision (voir plus bas).
- **Sauvegarde** : tes calculs vers un dossier synchronisé ou un stockage en ligne (voir plus bas).
- **Importer des mains** : glisse tes historiques ou choisis-les — des fichiers `.txt`, un dossier (avec ses
  sous-dossiers) ou une **archive `.zip`** avec ses dossiers (et les archives qu'elle contient). Chaque historique
  qui apporte des mains entre dans la base de données (son texte d'origine compris, voir « Base de données ») ; les
  doublons, les formats non reconnus et les autres fichiers de l'archive (PDF, images…) sont signalés. Une
  archive : 20 000 historiques et 1 Go décompressé au plus, 200 Mo par envoi (une archive de 150 Mo environ). Le
  dossier des mains (`hands/` par défaut) reste une boîte d'arrivée : un historique ou un `.zip` qu'on y dépose
  est importé au lancement de l'application.

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

Ton pseudo est détecté automatiquement (tag `Hero` de Betclic, « Dealt to » de Winamax, compte entre crochets
d'Unibet) ; sinon passe `--hero TonPseudo`.
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
`--threads`). Chaque résolution est enregistrée dans la base : un coup déjà résolu s'affiche tout de suite.

**Le spot** construit pour une main :

- ranges de départ tirées de la solution préflop : SRP (open du bouton / call de la BB), pot 3bet
  (3bet de la BB / call du bouton), pot 4bet (4bet du bouton / call de la BB) ;
- board, pot et tapis effectif au flop, en bb, sans rake ;
- tailles **théoriques**, une par situation (c-bet, 2e barrel, c-bet retardée, probe, check-raise…) : celles
  choisies pour ce flop comme pour les spots d'étude (voir *Choix des tailles* plus bas), dans la série du même
  jeu. Un coup heads-up prend la série heads-up de même structure (SRP où l'ouvreur est en position, pot 3bet où
  le 3bettor est hors de position, pot 4bet où le 4bettor est en position). Un coup d'une table à plusieurs
  prend d'abord la série 6-max de ses positions (BB contre BTN en pot 3bet…), sinon une série 6-max de même
  structure (BB contre HJ prend BB contre BTN), et seulement s'il n'y en a pas encore, la série heads-up de même
  structure. Tant que ce flop n'a pas ses propres tailles, celles du flop choisi le plus proche (même texture et
  mêmes couleurs d'abord). Pour garder l'arbre léger, une seule taille par situation à la river (la plus
  employée des deux choisies) et pas de relance à la river, sauf jouée : l'arbre d'un SRP compte environ 600 000
  nœuds (2 Go, 3 minutes sur 4 cœurs) au lieu de 1,7 million avec toutes les tailles de l'étude. Pas de donk
  (mise d'ouverture hors de position dans l'agresseur de la street précédente), deux relances au plus par
  street. Sans série de même structure (3bet du bouton en heads-up…), les tailles par défaut : 33 % au flop,
  75 % à la turn et à la river, relance 60 % ;
- **les tailles jouées** dans la main s'ajoutent à la théorie, dans la situation où elles ont été jouées (ou
  la remplacent quand elles en sont à moins de 10 points), pour que chaque décision tombe sur une branche de
  l'arbre et que l'EV de chaque taille juge ton choix de taille. L'explorateur le dit en tête (« Toi ·
  C-bet 66 % : taille jouée, ajoutée à l'arbre à côté de la théorie (33 %) ») et marque l'action « jouée » :
  la part de la range sur chaque taille compare ces options, ce n'est pas un mélange à reproduire ;
- **Choisir les tailles de ce flop** (bouton en tête de l'explorateur, quand les tailles sont empruntées) :
  le choix exact pour ce flop, comme pour un spot d'étude (1 h 10 environ en SRP, 25 minutes en pot 3bet,
  2 minutes en pot 4bet sur 4 cœurs), puis le coup se résout avec elles ; ensuite le spot d'étude de ce flop
  se résout à son tour et rejoint sa série dans *Études du solveur*. Les analyses en lot (revue, leakfinding)
  prennent les tailles du flop le plus proche ; une main déjà analysée avec les anciennes tailles fixes garde
  son analyse.

**La lecture**, pour chaque décision : la stratégie du solveur avec ta main exacte, avec toute ta range,
l'EV de chaque action et la perte d'EV de ton choix ; le même verdict qu'au préflop (action principale,
secondaire ou écart) ; une grille 13 × 13 de la range de celui qui agit. Sa main n'apparaît que si le
replayer la dévoile.

**L'explorateur** (*Ouvrir l'explorateur ↗* dans le panneau du solveur, ou `/explorateur/<numéro de main>`)
s'ouvre dans une nouvelle fenêtre, comme un solveur :

- en haut, le déroulé du coup, comme dans Wizard : l'action préflop jouée (un clic ouvre la solution
  préflop à ce moment), puis chaque nœud avec ses actions (● = action jouée dans la main), le tapis
  effectif du joueur au-dessus de ses actions et le pot sur chaque street (FLOP, TURN, RIVER) ; clique sur
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
- un clic sur une case du déroulé ramène à ce moment du coup.

Les mises sont en % du pot ; les relances aussi, selon la convention des solveurs : le montant ajouté
rapporté au pot après le call (relancer à 4,5 sur une mise de 1,7 dans un pot de 5 = 2,8 / 8,4 = 33 %).

**Tes ranges préflop** (onglet *Ranges* de l'explorateur) : le solveur part des ranges de la référence (solution
préflop heads-up, ou ta solution du format à une table à plusieurs). Pour voir ce que change une autre range — un
adversaire qui défend plus large, un open plus serré, une réponse au 4bet qui dépend de l'adversaire —, ajuste
celle de chaque joueur : grille 13 × 13 à peindre au clic ou en glissant (100, 75, 50, 25 % ou retirer), texte à
coller (« AA,AKs,KQo:0.5,… »), *Plus serré* / *Plus large* (environ 10 % des combos retirés en partant des mains
les plus faibles de la range, ou ajoutés en partant des plus fortes ; force = équité contre une main au hasard).
Contour orange : différent de la référence. Deux portées : **pour ce coup seulement** (ou ce spot d'étude), ou
**par défaut pour la ligne** et le format (« BTN open, BB call » en HU, « CO open, BB 3bet, CO call » en 6-max…) :
elle s'applique alors à tes autres coups de cette ligne, à leur analyse et au leakfinding ; le réglage d'un coup
passe avant celui de sa ligne. *Résoudre avec ces ranges* relance le solveur sur une étude à part : celle de la
référence reste, et *Revenir à la référence* la rouvre en quelques secondes. Un badge *tes ranges* le rappelle en
haut de l'explorateur, et la page *Études du solveur* marque ces études. Les séries de spots d'étude, leurs plans de
jeu et la référence livrée restent faits avec la théorie. Tes ranges sont gardées dans la base (sauvegardée) ;
code : `theory/custom_ranges.py`.

**L'EV** est en bb, à partir du moment du coup affiché : un fold vaut 0, le pot déjà au milieu est à gagner
et les mises à venir sont dépensées. Pour une action (au survol), c'est ce que rapporte cette action puis la
suite jouée par le solveur ; pour une main (dans la grille), c'est l'EV de sa stratégie, la moyenne de ses
actions pondérée par leurs fréquences. Une main qui folde 100 % vaut donc 0 même si payer coûterait 14 bb.

**Mains que le solveur ne joue presque jamais à un nœud.** La fréquence d'une main est une moyenne sur les
itérations, pondérée par sa présence au nœud. Une main qui n'y arrive presque jamais (hors de la range, ou
une ligne que le solveur ne prend pas avec elle : moins d'une fois sur cent, ou mille fois moins présente
que les autres) n'y apprend rien ; sa fréquence est un reste des premières itérations, arrondi par le
stockage compressé (ex. : fold 36 % alors que payer rapporte 11 bb). Son EV par action, elle, est calculée
face à la stratégie finale de l'adversaire. Pour ces mains, l'explorateur montre donc la meilleure action
selon l'EV (nom de la case en italique, note dans le détail du combo), et la revue des mains juge la décision sur
l'EV perdue. Quand c'est la range entière qui n'arrive presque jamais à un nœud (moins de 0,5 %), un
avertissement le signale : la suite du coup n'y est pas optimisée.

**Études du solveur** : chaque coup résolu est gardé : sa fiche dans la base, son arbre dans un fichier
(`~/.analyzer/etudes`, 50 à 170 Mo selon l'arbre) sous une forme compacte : la stratégie de chaque nœud sur 8 bits,
compressée.
L'explorateur rouvre une étude en une quinzaine de secondes au lieu de la recalculer (les fréquences
restent à 1 point près, les EV à quelques centièmes de bb). La page *Études du solveur* de l'application
liste les études (onglet *Coups joués*), avec leur précision et leur taille, et permet de les rouvrir ou
de les supprimer.
Une étude ouverte occupe environ 2 Go de mémoire : une seule reste ouverte, fermée après 30 minutes sans
activité ou quand une autre s'ouvre.

**Durée** : un arbre de flop compte des centaines de milliers de nœuds et jusqu'à 2 Go de mémoire avec les
ranges HU complètes. Sur un processeur à 4 cœurs, l'objectif par défaut (1,5 % du pot d'exploitabilité,
120 itérations au plus) demande 30 secondes à 2 minutes pour un SRP, davantage quand des tailles jouées
s'ajoutent à l'arbre ; c'est plus rapide avec plus de cœurs.

**Précision et durée estimée** : la précision visée (exploitabilité en % du pot : 3, 2, 1,5, 1 ou 0,5) se règle
dans l'explorateur, à côté de *Résoudre*, et en tête de chaque série de spots d'étude ; elle vaut pour les
résolutions suivantes (explorateur, séries, analyse des mains, leakfinding). Chaque choix affiche sa durée
estimée pour ce spot : la taille de l'arbre (`analyzer-solve --taille`, sans résoudre) × la vitesse de cet
ordinateur × les itérations qu'il faut pour descendre à cette précision. Vitesse et itérations viennent des
dernières résolutions faites ici (notées dans la base) ; avant la première, de repères mesurés sur 4 cœurs
(environ 3 milliardièmes de seconde par itération, par nœud et par combo ; 1,5 % vers 45 itérations, 0,5 % vers
70). Un spot déjà résolu se rouvre à sa précision, quelle qu'elle soit (la dernière employée est notée dans la
base) ; s'il est moins précis que le réglage, *Affiner* le résout à nouveau. Le choix
des tailles d'un flop garde sa propre précision (0,4 % du pot au flop).

Ce qui joue sur la durée, mesuré sur 4 cœurs :

- la largeur des ranges : le même arbre de SRP sur K♠K♦4♣ (340 000 nœuds) se résout en 64 s avec les ranges
  6-max BB contre BTN, 122 s avec les ranges heads-up ;
- la taille de l'arbre : chaque taille de plus multiplie les branches (d'où une seule taille river dans
  l'arbre des coups joués). Un pot où l'ouvreur est hors de position (SB contre BB) a un arbre plus gros, les
  deux joueurs pouvant miser au flop (c-bet de la SB, stab de la BB) : 596 000 nœuds contre 342 000 pour un SRP
  ouvert en position, et une convergence plus lente (90 itérations pour 1,5 % au lieu de 60) ;
- la variante de CFR : GTOpen propose aussi CFR+ et CFR+ prédictif ; sur nos spots, DCFR (celle utilisée)
  atteint l'objectif en moins d'itérations (pot 3bet : 72 s contre 84 s en CFR+ et 124 s en CFR+ prédictif ;
  SRP : 172 s contre 348 s en CFR+ prédictif) ;
- une carte NVIDIA (`--gpu`) : selon GTOpen, quelques dizaines de millisecondes par itération sur un arbre de
  1,35 million de nœuds ;
- ce qui est déjà réutilisé : un flop identique aux couleurs près (même stratégie, une seule étude), une étude
  enregistrée qui se rouvre en quelques secondes, les tailles choisies du flop le plus proche. Repartir d'une
  solution *proche* (autre flop, autres ranges) n'est pas proposé : GTOpen ne reprend que le même arbre, et
  initialiser un autre jeu à partir d'une stratégie voisine ne garantit pas de gagner du temps.

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
python -m analyzer gtopen --spots srp                          # les 24 flops : choix des tailles, puis résolution
python -m analyzer gtopen --spots 3bet                         # la même chose en pot 3bet
python -m analyzer gtopen --spots 4bet                         # et en pot 4bet
python -m analyzer gtopen --spots srp --texture Monotone --texture "Ace high"   # quelques textures
```

La série alterne les textures (un flop de chaque, puis un deuxième…) : interrompue, elle couvre déjà
toutes les textures, et une relance reprend où elle s'était arrêtée. Compter 30 secondes à 2 minutes par
flop sur 4 cœurs (la série : une demi-heure) et 50 à 80 Mo sur le disque (1,6 Go pour les 24 flops).
Sans attendre, la page montre déjà une synthèse de référence livrée avec Analyzer
(`analyzer/theory/data/srp_reference.json`, calculée avec le même arbre) pour les flops pas encore
résolus ; il faut les résoudre sur ton ordinateur pour les explorer. La page *Études du solveur* montre ensuite, texture par texture
(avec la moyenne de ses flops), la stratégie de toute la range : la c-bet du bouton après le check de la
BB, la réponse de la BB (fold, call, check-raise), puis celle du bouton face au check-raise. *Explorer ↗*
ouvre le spot dans l'explorateur (`/explorateur/spot:srp:KsKd4c`), turn et river comprises : l'étude se
rouvre d'elle-même, le choix de la carte s'ouvre quand on arrive à la turn ou à la river, et les checks
forcés de la BB (qui ne mène pas) sont passés pour aller droit à la décision suivante.
*Étudier un autre flop* ouvre n'importe quel flop dans l'explorateur ; une fois résolu, il rejoint sa
texture dans la page.

**Explorateur depuis le préflop** : l'onglet *Explorateur* de *Études du solveur* (ou `/explorateur/preflop`)
part de l'open du bouton. Le déroulé montre les actions préflop de la solution (fold, open 2,5, call, 3bet
11,5, 4bet 26, tapis) et la grille leurs fréquences main par main, pour le joueur qui agit ou la range de
l'autre. Au call, on choisit le flop : SRP après le call de l'open, pot 3bet après le call du 3bet, pot 4bet
après le call du 4bet. On choisit ses trois cartes parmi les 52 : Analyzer propose alors les flops déjà
résolus qui s'en approchent, à ouvrir tout de suite (le même flop aux couleurs près, qui a la même stratégie ;
les mêmes hauteurs avec la même structure de couleurs, rainbow, deux couleurs ou monotone ; la même texture),
ou résout ce flop (*Résoudre ce flop* : une dizaine de minutes en SRP, 2 à 3 en pot 3bet, moins d'une en pot
4bet sur 4 cœurs ; l'étude est gardée). Un flop hors série prend les tailles du flop le plus proche dont les
tailles sont choisies (même texture et même structure de couleurs d'abord) ; un flop de la série jamais
résolu passe d'abord par son propre choix des tailles. La liste des flops de la série (surlignés quand ils sont
résolus) reste en dessous. Le spot s'ouvre alors avec ses actions préflop en tête
du déroulé : un clic y revient au préflop, *changer* sous le flop ramène au choix du flop, et *◀ Retour*
au début du spot aussi. Les autres onglets de *Études du solveur* séparent les séries (SRP, pots 3bet,
pots 4bet) et les coups joués résolus : plus besoin de dérouler toute la page.

**Un coup à plusieurs** : en haut de l'explorateur, *Heads-up* / *À plusieurs (6-max)* (le dernier choisi est gardé ;
`/explorateur/preflop#table=6-max` ; sans tes charts 6-max, un bouton les charge, même sans main à plusieurs). À plusieurs, le déroulé suit l'ordre de parole d'une table 6-max (UTG, HJ, CO,
BTN, SB, BB) et la grille montre les fréquences de tes charts 6-max pour la position qui parle : premier à parler,
open ou fold ; face à l'open, fold, call ou 3bet (le premier qui paie ou relance reste seul face à l'ouvreur, les
suivants se couchent : les charts couvrent les pots à deux) ; face au 3bet, fold, call ou 4bet ; face au 4bet, fold ou
call. Tailles des spots d'étude 6-max : open à 2,5 bb, 3bet à 7,5 bb en position et 10 bb hors de position, 4bet à
22 bb en position et 20 bb hors de position. Au call, on choisit le flop comme en heads-up, pour **n'importe quelle
paire de positions** (le HJ contre le BTN, l'UTG contre la BB…), pas seulement celles des séries 6-max : hors des
séries, les tailles viennent du flop le plus proche d'une série de même structure (qui a l'initiative, en position ou
non). Le spot s'ouvre avec la ligne préflop des charts en tête du déroulé ; ces études se retrouvent dans
*Études du solveur › 6-max* (*Autres positions*) et dans le choix du flop (`theory/ring_tree.py`).

**Choix des tailles** (`analyzer/theory/sizing.py`) : chaque flop de la série a ses propres tailles, une par
situation pour tout le flop (quelles que soient la turn et la river), deux à la river. Pour chaque situation,
la taille retenue est celle qui donne la meilleure EV à celui qui mise (ou relance) quand c'est sa seule
option ; si l'écart est minime (moins de 0,4 % du pot au flop, soit 0,02 bb en SRP), la plus petite l'emporte.
Les candidates :

| Situation | Tailles comparées |
| --- | --- |
| C-bet | 33 % · 75 % · géométrique |
| 2e barrel (c-bet payée) | 50 % · pot · géométrique |
| C-bet retardée (flop checké) | 33 % · 66 % · géométrique |
| Probe turn de la BB (flop checké) | 33 % · 75 % · pot · géométrique |
| Mises de la BB et du bouton après un check-raise payé | 50 % · pot · géo / 33 % · 66 % · géo |
| River : 3e barrel, bet/check/bet, check/bet/bet, probes river… | deux parmi 50 % · 75 % · pot · 150 % · tapis |
| Relances et sur-relances (check-raise, relance d'un probe…) | 33 % · 66 % · géométrique (tapis à la river) |

Le géométrique mise la même fraction du pot à chaque street restante pour finir à tapis à la river
(121 % au flop d'un SRP à 100 bb ; à la turn, selon la ligne). Les relances se comptent en % du pot après
le call. La BB ne mène toujours pas dans l'agresseur (pas de donk).

Méthode, street par street, chaque situation à son tour (les autres gardent leur choix courant) :

- flop : arbres complets, comparés à 0,4 % du pot près (c-bet, check-raise, relance du bouton) ;
- turn, puis river : sous-jeux qui partent de la turn avec les ranges du flop résolu, sur 12 cartes turn
  (`--cartes`), 8 pour la river ; l'écart d'EV se juge quand la situation arrive (l'écart à la racine
  du sous-jeu, rapporté à sa fréquence) : sous 0,5 % du pot, la plus petite taille l'emporte. Une
  situation rare se compare sur 4 cartes ; presque jamais atteinte (moins de 0,3 % des coups), elle prend
  la plus petite taille. À la river, une première résolution propose les cinq tailles à la fois : les
  trois plus employées restent, et leurs trois paires se comparent.

Mesuré sur K♠K♦4♣ avec 4 cœurs : 35 minutes pour le flop, 5 pour la turn, une demi-heure pour la river,
puis une dizaine de minutes pour résoudre le flop avec ses tailles (arbre d'environ 1,2 million de nœuds,
4 Go de mémoire, étude de 430 Mo) : compter une nuit pour une dizaine de flops, moins avec plus de cœurs. Le
choix est gardé dans la base ; ceux calculés à l'avance sont livrés avec Analyzer
(`analyzer/theory/data/srp_tailles.json`) et ne se refont pas. La page *Études du solveur* montre sous
chaque flop ses tailles et, en dépliant, l'EV de chaque taille comparée. Le bouton **Résoudre les flops
manquants** choisit les tailles qui manquent avant de résoudre ; en ligne de commande, `--spots srp` fait
de même, `--choix-seulement` s'arrête au choix, `--sans-choix` garde les tailles par défaut.

**Pots 3bet** : la deuxième série (`--spots 3bet`, section *Spots d'étude · pot 3bet* de la page) reprend les
mêmes 24 flops avec le 3bet de la solution préflop : open du bouton à 2,5 bb, 3bet de la BB à 11,5 bb, call
du bouton ; pot de 23 bb, 88,5 bb derrière. La BB est hors de position et à l'initiative : c-bet, 2e barrel
et c-bet retardée sont les siens ; le bouton *stabbe* quand elle checke. Pas de donk : la BB ne mène pas
dans le bouton quand il a misé à la street précédente. Les tailles comparées :

| Situation | Tailles comparées |
| --- | --- |
| C-bet de la BB | 33 % · 75 % · géométrique sur deux streets (97 % : tapis à la turn) |
| 2e barrel | 33 % · 50 % · 75 % · tapis |
| C-bet retardée (flop checké) | 33 % · 75 % · géométrique (turn et river) |
| River de la BB : 3e barrel, bet/check/bet, probe… | deux parmi 33 % · 50 % · 75 % · tapis |
| Stab du bouton (la BB checke), à chaque street | 25 % · 50 % · tapis |
| Le bouton qui continue après sa mise payée | 33 % · 50 % · 75 % · tapis (deux à la river) |
| Relances et sur-relances | 33 % · 66 % · tapis |

La synthèse de chaque flop montre la c-bet de la BB, la réponse du bouton, la BB face à sa relance, puis le
stab du bouton et la réponse de la BB. La méthode est celle du SRP ; au flop, six situations se comparent
sur arbres complets (c-bet, relance du bouton, stab, check-raise de la BB, sur-relances). Les arbres sont
plus petits (moins de jetons derrière) : mesuré sur K♠K♦4♣ avec 4 cœurs, 26 minutes pour le choix des
tailles (21 pour le flop, 1 pour la turn, 4 pour la river) et 2 min 30 pour la résolution (830 000 nœuds,
1,5 Go de mémoire, étude de 150 Mo), soit une douzaine d'heures pour la série. Les tailles de K♠K♦4♣ sont
livrées (`analyzer/theory/data/3bet_tailles.json`).

**Pots 4bet** (`--spots 4bet`) : open du bouton à 2,5 bb, 3bet de la BB à 11,5 bb, 4bet du bouton à 26 bb,
call de la BB ; pot de 52 bb, 74 bb derrière (SPR 1,4). Le bouton est en position et à l'initiative, comme
en SRP ; la BB ne mène pas. À cette profondeur, 25 % est presque le géométrique sur trois streets (28 %),
le géométrique sur deux streets fait 48 % et le tapis 142 % du pot. Après le flop, le géométrique retombe
vers 20-30 % du pot : la turn et la river prennent donc 50 % comme taille intermédiaire.

| Situation | Tailles comparées |
| --- | --- |
| C-bet | 25 % · géométrique sur deux streets (48 %) · tapis |
| Turn : 2e barrel, c-bet retardée, probe | 25 % · 50 % · tapis |
| River | deux parmi 25 % · 50 % · tapis |
| Relances | 33 % · tapis (une relance au-delà de 85 % du tapis devient le tapis) |

Les arbres sont petits : sur 4 cœurs, une minute et demie pour choisir les tailles d'un flop et un quart de
minute pour le résoudre (310 000 nœuds, étude de 20 Mo). Les tailles et la synthèse des 24 flops sont
livrées (`analyzer/theory/data/4bet_tailles.json`, `4bet_reference.json`).

**Spots d'étude 6-max** (onglet *6-max* de *Études du solveur*) : les tables à plusieurs sont un autre jeu que
le heads-up, aux ranges bien plus serrées (la BB défend environ 350 combos contre l'open du bouton en 6-max, bien
plus en heads-up). Ces séries reprennent les 24 flops des séries heads-up, pour comparer les deux jeux flop par
flop, avec les ranges de tes charts 6-max (dans la base, voir *Tables à plusieurs*) :

| Positions | Pots | Structure (qui a l'initiative) |
| --- | --- | --- |
| SB contre BB | SRP, 3bet, 4bet | la SB ouvre et 4bette (hors de position) ; la BB 3bette en position |
| SB contre BTN | 3bet, 4bet | la SB 3bette hors de position, le bouton 4bette en position |
| BB contre BTN | SRP, 3bet, 4bet | le bouton ouvre et 4bette, la BB 3bette |
| BB contre CO | SRP, 3bet, 4bet | le CO ouvre et 4bette, la BB 3bette |
| BB contre HJ | SRP, 3bet | le HJ ouvre, la BB 3bette |

Tailles préflop (les charts donnent les ranges, pas les tailles) : open à 2,5 bb, 3bet à 3 fois l'open en
position (7,5 bb) et 4 fois hors de position (10 bb), 4bet à 22 bb en position et 20 bb hors de position,
100 bb ; la blinde d'un joueur qui a foldé reste au pot. Les tailles postflop se choisissent flop par flop comme
en heads-up ; trois structures n'existent pas en heads-up et ont leurs propres candidates : SRP ouvert hors de
position (SB contre BB : celles d'un SRP), pot 3bet du joueur en position (c-bet 33 / 75 % / géo sur deux
streets) et pot 4bet hors de position (25 % / géo / tapis). Une famille : `6max_<hors de position>_<en
position>_<pot>` (`python -m analyzer gtopen --spots 6max_bb_btn_srp`, explorateur
`/explorateur/spot:6max_bb_btn_srp:KsKd4c`). Les ranges plus serrées raccourcissent les calculs : mesuré sur
K♠K♦4♣ avec 4 cœurs, BB contre BTN en pot 3bet demande 12 minutes pour choisir les tailles et 81 secondes pour
résoudre (26 minutes et 2 min 30 en heads-up), et les tailles changent (c-bet de la BB à 75 % au lieu de 33 %
en heads-up). Le plan de jeu suggéré, le coach et le leakfinding 6-max (ses repères après le flop) lisent aussi
les séries 6-max ; l'entraîneur travaille sur les séries heads-up. Leurs résultats viennent de tes charts et restent
sur ta machine (rien n'est livré avec Analyzer). Les autres paires de positions (l'UTG contre la BB, le CO
contre le BTN…) n'ont pas de série : elles se résolvent flop par flop depuis l'explorateur (*À plusieurs (6-max)*),
et s'ajoutent à la liste *Autres positions* de l'onglet.

**Toutes les séries d'un coup** : le bouton **Résoudre tous les flops 6-max manquants** (en haut de l'onglet 6-max de
*Études du solveur*) met en file chaque flop manquant de toutes les séries que tes charts couvrent, un flop de
chaque série à tour de rôle, les pots 4bet et 3bet d'abord (les plus rapides) : chaque plan de jeu se dessine vite
au lieu d'attendre la fin des séries précédentes. Chaque flop passe par le choix de ses tailles puis par sa
résolution, et son plan de jeu est lu aussitôt. Avant de lancer, la durée est annoncée (au plus 8 jours environ
sur 4 cœurs pour les 312 flops des treize séries ; les séries en SRP sont les plus longues) ; on suit le flop en cours et la file, et **Tout arrêter** vide la file (les
flops déjà résolus restent). La file vit dans l'application : après un redémarrage, le bouton reprend avec les
flops qui manquent.

**Limites** : les tailles et la profondeur de l'arbre simplifient le jeu réel ; une main que la range du
solveur ne contient pas (par exemple un open que le solveur ne fait jamais) y est ajoutée avec un poids
infime pour lire sa stratégie, à prendre avec prudence ; les pots limpés et les 5bets ne sont pas couverts.

**Tailles selon la ligne** : GTOpen fixe les tailles par street et par joueur seulement (la même mise du
bouton à la turn après une c-bet payée ou après un flop checké). `analyzer-solve` sait construire lui-même
l'arbre (`analyzer/theory/native/arbre.rs`, mêmes règles que GTOpen) quand la requête porte un *plan* :
une liste de tailles par situation, c-bet, 2e barrel, c-bet retardée, probe, bet/check/bet, check-raise…
(clés décrites en tête du fichier). Sans plan, l'arbre est celui de GTOpen ; avec un plan vide, celui
d'Analyzer lui est identique nœud pour nœud (`analyzer-solve --verifier-arbre requete.json`). C'est la
base du choix des tailles par situation et de la modification des tailles d'un spot.
Après une mise à jour d'Analyzer qui touche ce pont, relance `python -m analyzer gtopen --installer`.

Variables d'environnement : `ANALYZER_HOME` (dossier de travail, `~/.analyzer` par défaut),
`GTOPEN_DIR` (copie de GTOpen à utiliser), `ANALYZER_SOLVER` (chemin d'un `analyzer-solve` déjà compilé).

## Face au solveur (mains jouées)

L'onglet *Face au solveur* (dans « Mon jeu » et pour chaque adversaire) compare tes mains allées au flop à la
théorie. Chaque main (SRP, pot 3bet ou 4bet ; pas les limps ni les tapis préflop) est résolue une fois, sur
sa ligne réelle, avec les tailles de l'arbre par défaut ; on en garde un résumé de quelques Ko (dans la base de
données), qui survit aux mises à jour du solveur.

```bash
python -m analyzer gtopen --analyser            # toutes les mains pas encore analysées
python -m analyzer gtopen --analyser --max 40   # les 40 plus gros pots seulement
```

**Réguliers et récréatifs** : contre un récréatif, le bon jeu est l'exploitation, pas la théorie. Le type de
chaque adversaire se règle en haut de sa fiche (*Auto*, *Régulier*, *Récréatif*) ; en *Auto*, Analyzer le
suggère d'après ses stats (récréatif quand au moins deux signaux concordent : il limpe plus de 20 % de ses
boutons, ouvre moins de la moitié, folde plus de 55 % de ses BB face à l'open, ne 3bette presque jamais,
folde trop face au 3bet, très passif après le flop), sinon régulier. Les mains contre les récréatifs sortent
des comparaisons à la théorie : *Face au solveur* et *Mon préflop* de « Mon jeu », tes décisions sur sa fiche
(qui ne garde que ses écarts à exploiter) ; le *Bilan* sépare tes résultats contre les deux types, et
`--analyser` les laisse de côté (`--recreatifs` pour les inclure, pour lire leurs écarts). Le type est gardé
par joueur (dans la base de données, incluse dans les sauvegardes) : le même aux tables à plusieurs, où la suggestion
lit ses fréquences à la taille de table où il a le plus joué (récréatif quand au moins deux signaux concordent : il
joue trop de mains, limpe, paie trop d'ouvertures, ne folde presque jamais face à la c-bet, paie beaucoup sans
relancer). Il se règle aussi dans *Étude du field › Les joueurs* et dans le Leakfinding.

Ou bouton **Analyser les mains restantes** dans l'onglet (la page se complète au fur et à mesure, on peut la
fermer). Les plus gros pots passent d'abord. Compte 3 minutes par SRP, 2 par pot 3bet et un quart de minute
par pot 4bet sur 4 cœurs : une vingtaine d'heures pour 430 mains, à étaler sur plusieurs nuits.

- **Les erreurs qui coûtent le plus** : décision par décision, l'EV que l'action jouée rapporte de moins
  que la meilleure action pour ta main (en bb). Une action que le solveur joue au moins 10 % du temps avec
  cette main ne coûte rien (à l'équilibre, les actions mélangées se valent ; un écart d'EV entre elles vient
  d'un nœud profond pas tout à fait convergé). Seuil d'erreur : 0,25 bb. **Revoir ↗** ouvre la main
  dans l'explorateur à cette décision.
- **Les erreurs récurrentes** : par situation de la ligne (c-bet, face à la c-bet, 2e barrel, probe,
  check-raise… les mêmes que pour le choix des tailles), le nombre de fois, les erreurs, l'EV perdue, et tes
  fréquences fold / passif / agressif face à celles qu'aurait eues le solveur avec toute sa range dans les
  mêmes coups.
- **Ses écarts à exploiter** (page d'un adversaire) : ses fréquences dans chaque situation face à la
  théorie. Ses cartes ne sont pas nécessaires : toutes ses décisions comptent. Un écart s'affiche à partir
  de 8 occurrences et 10 points ; « solide » quand le hasard l'explique très mal (écart de plus de 2,5
  écarts-types), « indicatif » sinon, avec la façon d'en profiter (il folde trop face à la c-bet : bluffe
  plus…). **Ses erreurs connues** : l'EV qu'il a laissée quand ses cartes ont été montrées.

## Leakfinding (toi et tes élèves)

Le rapport de ce qu'un joueur doit travailler en priorité : onglet *Leakfinding* de *Mon jeu* pour toi, et menu
*Élèves* pour tes élèves. Chaque élève a son espace dans la base de données (ses historiques et ses mains,
sauvegardés ; son dossier `~/.analyzer/eleves/<élève>/` sert de boîte d'arrivée) : ajoute-le
(nom, et son pseudo à la table si tu le connais), importe les historiques qu'il t'envoie (Betclic, Winamax ou Unibet), et son
rapport se construit. **Deux rapports** : *Heads-up* et *Tables à plusieurs* (toutes ses tables de 3 à 9 joueurs
ensemble ; les boutons en haut de la page, le dernier choisi est gardé), voir plus bas pour les tables à plusieurs.

- **Les leaks à travailler** : les cinq plus importants, avec leur preuve chiffrée, leur confiance et la façon de les
  travailler (entraîneur sur la situation, onglet Préflop, plan de jeu) ; pour une perte face au solveur, la main la
  plus chère à revoir. Classés par confiance puis par poids : fréquence de la situation × écart × ce que la décision
  met en jeu (le pot double à peu près à chaque street et grossit dans les pots 3bet et 4bet : la même erreur pèse
  plus à la river qu'à l'open), ou EV perdue. Seules les mains contre les réguliers comptent : contre un récréatif,
  l'exploitation prime.
- **Ses stats face à la théorie**, sur toutes ses mains, contre les réguliers et contre les récréatifs : d'abord
  **les écarts les plus importants** (situation, sa fréquence, la théorie, l'écart en points, la confiance et quoi
  faire), puis **le détail**, replié : VPIP, PFR, abattage, résultat, puis préflop face à la solution HU 100 bb (open,
  limp, 3bet, 4bet, folds) et après le flop face aux plans de jeu des flops résolus (c-bet, barrels, c-bet retardée,
  folds face aux mises, relances, probes), par type de pot et par position. Écart « solide » (le hasard l'explique
  mal : intervalle de confiance à 90 % hors d'une marge de 4 points autour de la théorie) ou « indicatif » (8 points
  d'écart) ; près de 0 % ou de 100 %, la marge rétrécit (un open à 23 % au lieu de 16 % est un gros écart).
- **Face au solveur** : ses décisions dans les mains choisies contre les réguliers, comparées à la meilleure action
  pour sa main exacte (EV perdue par situation, décisions les plus chères). Un bouton les fait toutes résoudre
  (environ 2 à 3 minutes par main).
- **Mains à revoir** : les plus gros pots de chaque ligne (type de pot, position, dernière street jouée), deux par
  ligne, contre les réguliers (avec l'avis du solveur) et contre les récréatifs (à revoir à la main).
- **Ses adversaires** : leur type (régulier ou récréatif), suggéré d'après leurs stats et réglable ; il décide quelles
  mains comptent pour les leaks. Recherche par nom, filtre par type, tri par mains, résultat ou nom.
- **Télécharger le rapport** : la même page en fichier autonome, sans les boutons de l'application, à envoyer à
  l'élève. Le coach a l'outil *leakfinding* : « Écris le rapport de coaching de Paul » dans l'application Claude
  rédige un rapport à partir de ces données.

L'élève a aussi ses onglets *Préflop* (face à la solution, main par main), *Mains de départ*, *Face au solveur*
(toutes ses mains contre les réguliers) et *Mains* (le visualiseur, d'où chaque main s'ouvre dans l'explorateur).

Les leaks comptent aussi les **mains de départ qui perdent plus que le fold** contre les réguliers (une main, ou
sa famille, jouée au moins 15 fois de la même façon, à la même position, et nettement sous le fold même en
tenant compte du hasard) : les trois plus coûteuses, avec **d'où vient la perte** (le type de pot qui coûte le plus,
et l'EV perdue selon le solveur sur ceux déjà analysés), l'avis de la théorie (« folde-la ici » si elle ne la joue
presque jamais ainsi, « revois la suite du coup » si elle la joue), le coup le plus cher à revoir et un lien vers
le détail de la main dans *Mains de départ*.

### Leakfinding aux tables à plusieurs

Le même rapport et la même présentation qu'en heads-up, sur toutes ses mains aux tables de 3 joueurs et plus
(3-max, 6-max, 7 à 9 joueurs, ensemble ; `analyzer/ring_leaks.py`) : stats générales puis le détail, sur toutes ses
mains, contre les réguliers et contre les récréatifs. Une main compte **contre les récréatifs** quand un récréatif a
mis de l'argent dans le pot de lui-même (call, relance, mise) pendant qu'il y était encore ; sinon contre les
réguliers. Seules les mains contre les réguliers font des leaks et passent au solveur ; contre les récréatifs, ses
écarts restent visibles et ses plus gros pots sont à revoir à la main.

- **Préflop, position par position, face aux charts avec les mêmes cartes** : l'open quand il parle le premier
  (et le limp), le fold, le call et le 3bet face à une ouverture, le fold et le 4bet face au 3bet après son open. Chaque
  décision est jugée par les charts de sa table, sinon par ceux du 6-max à même nombre de joueurs derrière : le
  3-max avec ceux du BTN, de la SB et de la BB ; à 7-9 joueurs, le LJ comme l'UTG (l'UTG à l'UTG+2 d'une table pleine
  n'ont pas de chart et ne sont pas comparés). La fréquence des charts est celle d'un joueur qui les suivrait avec les
  cartes qu'il a reçues : le hasard des cartes n'y est pour rien, la marge tolérée est donc deux fois plus petite
  (2 points pour « solide », 4 pour « indicatif »). Face au 3bet, seules comptent les mains que ses charts ouvrent.
  Sans charts (onglet *Tables à plusieurs* : « Charger les charts »), l'open est comparé aux repères indicatifs d'un
  régulier 6-max.
- **Après le flop, dans les pots à deux joueurs** (une ligne simple : open, 3bet ou 4bet, puis call ; personne d'autre
  n'a mis d'argent) : ses fréquences (c-bet, barrels, c-bet retardée, folds face aux mises et aux relances,
  relances, probe ou mise quand l'agresseur checke), à l'initiative ou en défense, par structure de pot — SRP,
  pot 3bet, pot 4bet, l'agresseur en position ou non — face à la moyenne des plans de jeu des flops 6-max résolus de
  même structure (*Études du solveur › 6-max*). Sans flop 6-max résolu, pas encore de repère après le flop : la page
  le dit.
- **Face au solveur** : ses plus gros pots à deux au flop contre les réguliers, deux par ligne (type de pot, sa
  position contre celle de l'adversaire, dernière street), passent au solveur avec les ranges des charts pour la
  ligne jouée ; les situations portent les vraies positions (« Stab flop du CO », pot 3bet SB c. CO).
- **Les mains de départ** qui perdent plus que le fold contre les réguliers, comme en heads-up, avec l'avis des charts.
- **Ses adversaires** des tables à plusieurs : mains à la même table, pots disputés ensemble, le résultat du joueur
  dans ces pots (« Ton résultat » dans ton Leakfinding, « Résultat de Paul » dans celui d'un élève) et leur type,
  réglable.

L'onglet *Tables à plusieurs* commence par **tes écarts les plus importants** contre les réguliers (les mêmes
repères), avec un lien vers ce rapport. Le coach lit aussi ce rapport (outil *leakfinding*, format `ring`).

## Mains de départ

Onglet *Mains de départ* de *Mon jeu* (et de chaque élève) : ce que rapporte ou coûte chaque main de départ, en
heads-up et aux tables à plusieurs (3 à 9 joueurs ensemble).

- **En tout** : le résultat de chaque main (grille 13×13, en bb par main), puis par position, et contre tous tes
  adversaires, contre les réguliers ou contre les récréatifs (aux tables à plusieurs, d'après la main : un
  récréatif a-t-il mis de l'argent dans le pot pendant que tu y étais ?).
- **À chaque décision préflop** : premier à parler (open, limp ou fold), après un limp, face à une ouverture
  (call, 3bet ou fold), face au 3bet, face au 4bet. Le résultat d'une décision est celui de toute la main qui suit,
  comparé au **fold à ce moment**, qui coûte ce que tu as déjà mis au pot : rien hors des blindes, la SB
  (−0,5 bb, −50 bb/100), la BB (−1 bb, −100 bb/100), ton open face au 3bet (−2,5 bb après un open à 2,5 bb,
  −250 bb/100). Ouvrir une main est rentable si elle gagne plus que le fold de ta position ; défendre la BB, si la
  main fait mieux que −100 bb/100 ; payer un 3bet, si elle fait mieux que −250 bb/100. En vert les mains qui font
  mieux que le fold, en rouge celles qui font moins bien.
- **Face à la théorie** : pour chaque main, la fréquence à laquelle la théorie joue cette action (la solution
  préflop en heads-up, tes charts aux tables à plusieurs), et un conseil : une main qui perd et que la théorie
  ne joue presque jamais ainsi est à couper ; une main que la théorie joue mais qui perd est à revoir après le
  flop (ou la variance) ; une main que tu foldes et que la théorie joue est signalée.
- **Mesure** : *EV all-in* par défaut (les tapis payés avant la river remplacés par leur espérance, pour ôter la
  chance ; en heads-up) ou résultat *réel*. Chaque moyenne a son intervalle à 95 % : sur quelques dizaines de mains
  la variance domine, d'où les **familles de mains** (paires hautes, as assortis, broadways, connecteurs…) qui
  tranchent plus vite.
- **Les mains et familles les plus problématiques** : les plus grosses pertes par rapport au fold, en tout (fois ×
  écart), à chaque décision et position ; « net » quand le hasard l'explique mal.
- **D'où vient la perte** (clic sur une main, une famille ou une case de la grille) :
  - la **suite du coup** : sans flop (ils foldent, tu foldes ensuite, tapis préflop) ou le pot au flop (limpé, SRP,
    3bet, 4bet, à plusieurs), avec la part de chacune dans l'écart au fold (les parts s'additionnent) ;
  - dans un pot, **ta main au flop** (deux paires ou mieux, top pair, paire moyenne, tirage, rien), **la fin du
    coup** (il folde, tu foldes à telle street, abattage gagné ou perdu) et ta position au flop ;
  - **tes choix face à la théorie** à cette décision et à la suivante (ex. après ton open : face au 3bet, tes folds,
    calls et 4bets contre ceux de la théorie) ;
  - l'**avis du solveur** sur les coups déjà analysés (EV perdue après le flop), et un diagnostic en quelques phrases ;
  - les **coups à revoir** : les plus chers du pot choisi, à rejouer (heads-up) ou à ouvrir dans le solveur.

  Le détail est calculé à la demande par l'application ; les liens du leakfinding y mènent directement.

## Bluffs des adversaires

L'onglet *Ses bluffs* (fiche d'un adversaire) et *Bluffs des réguliers* (*Étude du field*, tous les réguliers ensemble,
puis un par un) cherchent où un joueur bluffe : dans quelles lignes, sur quelles cartes, avec quelles tailles. Deux sources :

- **Ses fréquences**, sur toutes ses mains : à chaque situation (c-bet, 2e et 3e barrels, c-bet retardée, probe, mise
  quand l'agresseur checke, autres mises à la river), sa fréquence de mise selon la carte qui vient de tomber
  (overcard, couleur ou quinte possible, board qui se paire, brique) ou la texture du flop, face à celle du solveur
  dans les mêmes situations (moyenne des plans de jeu des flops résolus). Une carte ne lui donne pas plus de bonnes
  mains qu'à la théorie : s'il mise nettement plus que le solveur quand elle tombe, le surplus est fait de bluffs ou de
  value fine. Sans repère du solveur, on compare à ses autres cartes.
- **Ses mains montrées** : chaque mise ou relance vue à l'abattage est classée (value, value fine, semi-bluff, bluff)
  et rangée par ligne, taille et carte. À la river, la part de bluffs est comparée à celle de la théorie pour la
  taille de mise (l'équité qu'il te faut pour payer) ; au flop et à la turn, à sa propre moyenne. Le timing (temps de
  réflexion des bluffs et de la value) est regardé aussi.

*Ce qui ressort* liste les patterns, avec leur preuve chiffrée et la façon d'en profiter : « solide » quand le
hasard l'explique mal (intervalle de confiance à 90 %, au moins 10 occasions), « à confirmer » quand l'écart est net
sur peu de mains. Exemple sur un régulier : c-bet de 100 % sur les flops As-hauts et 95 % sur les flops pairés
(65 % et 62 % pour le solveur), mais 33 % sur les flops Dix-hauts (72 %) ; mise river 62 % quand le board se paire
contre 42 % sur les autres cartes. Le coach a le même outil (« Dans quelles lignes Villain bluffe-t-il ? »).

**Les réguliers ensemble** : un régulier à gros volume ne fait pas la moyenne à lui seul. Chaque fréquence (et chaque
part de bluffs montrés) est la moyenne des joueurs, chacun pesant n / (n + 20) selon ses occasions n (n / (n + 5) pour
les mises montrées) : au plus autant qu'un autre, et peu quand il a peu de mains. L'intervalle de confiance suit
l'effectif efficace de cette moyenne. Le tableau *Adversaire par adversaire* montre, pour chacun, sa part des
occasions et son poids réel. **Profils des réguliers** : les réguliers regroupés par façon de bluffer. Le profil d'un
joueur est son écart au solveur dans chaque situation (fréquence de mise à cartes égales) et sa part de bluffs
montrés à la river face à la théorie, rapproché de la moyenne des réguliers quand il a peu d'occasions (au moins 40
occasions pour avoir un profil). Deux groupes se réunissent tant que leurs profils diffèrent de moins de 7 points en
moyenne. Chaque groupe a son titre (ce qui le distingue des autres réguliers), ses écarts situation par situation et
ce qui ressort de ses mains.

## Plan de jeu suggéré

Onglet *Plan de jeu suggéré* de *Études du solveur* : les études résolues réduites à des règles simples, du
flop à la river, par type de pot (SRP, pots 3bet, pots 4bet). Plus il y a de flops résolus, plus il est précis.

- **Heads-up et tables à plusieurs** : un plan pour chaque série de spots d'étude, les trois du heads-up et les
  treize du 6-max (une par paire de positions et type de pot : SB contre BB, SB contre BTN, BB contre BTN, BB contre
  CO, BB contre HJ ; voir *Spots d'étude 6-max*). Les arbres 6-max ont la forme du heads-up de même structure (celui qui a
  l'initiative est hors de position ou en position) : mêmes lignes et mêmes règles, avec les vraies positions
  (« C-bet de la SB », « BB face à la c-bet », « CO face au check-raise »…). Les onglets 6-max apparaissent une
  fois tes charts 6-max chargés ; une famille que tes charts ne couvrent pas dit pourquoi.

- **Lecture des études** : chaque étude est ouverte une fois pour lire ses stratégies aux nœuds clés : c-bet,
  réponse à la c-bet et au check-raise, 2e barrel à chaque turn, 3e barrel sur un échantillon de rivers (13
  turns, toutes leurs rivers), c-bet retardée et probe après un flop checké. Les mains y sont regroupées par
  famille (deux paires et mieux, overpair, top pair bon ou petit kicker, paire moyenne, petite paire, tirage
  couleur, tirage quinte, gutshot ou backdoor, hauteur As ou Roi, rien), les turns et rivers par effet sur le
  board (overcard, brique, board pairé, couleur possible, quinte possible). Une étude résolue est lue tout de
  suite ; les anciennes, avec **Préparer le plan** (quelques secondes en pot 4bet, une minute environ en SRP).
  Résultats dans la base (sauvegardée).
- **En attaque** (celui qui a l'initiative) : au flop, **trois stratégies de c-bet**, chacune précisée par sa
  **taille** (petite, moyenne, grosse mise ou tapis, avec le % du pot de l'arbre) : *miser range* (c-bet de 70 %
  et plus : une mise avec presque toute la range), *stratégie mixte* (de 35 à 70 % : on mise une partie des mains,
  on checke les autres) et *checker range* (35 % et moins). Chaque groupe (stratégie et taille) montre sa
  fréquence de c-bet, son nombre de flops et des flops résolus en exemple, de catégories différentes (haut, moyen
  ou bas ; sec, deux couleurs ou connecté ; pairé ; monotone) : un clic ouvre l'étude dans l'explorateur pour
  entrer dans le détail. Une stratégie sans flop dit pourquoi (par exemple : le bouton mise toujours au moins 40 %). Le
  détail d'un groupe : tous ses flops, le pourquoi (avantage d'équité et de nuts), la règle au flop pour quatre
  familles de mains, *fortes* (deux paires et mieux, overpair, top pair bon kicker), *moyennes* (top pair petit
  kicker, paires moyennes et petites), *tirages* (couleur, quinte) et *rien* (hauteur, gutshots, backdoors), puis
  à la turn et à la river selon la carte (overcard, brique, board pairé, couleur ou quinte possible), avec la
  fréquence de mise ; repliés : la c-bet retardée, le jeu face au check-raise et le détail en onze familles.
- **En défense** (l'autre joueur), le même découpage vu d'en face : pour chaque groupe, ce que fait la défense
  face à la c-bet (fold, call, relance, et le fold au-delà duquel la mise rapporte d'elle-même : 25 % face à une
  mise de 33 % du pot), la règle par famille de mains, la défense face au 2e barrel selon la turn et au 3e barrel
  selon la river, et ce qu'elle fait quand l'attaquant checke (probe à la turn quand elle est hors de position,
  stab au flop quand elle est en position).
- **Les flops résolus** : chacun avec sa catégorie, sa stratégie, sa c-bet et sa taille, le fold de la défense
  face à la c-bet et les deux avantages ; un clic l'ouvre dans l'explorateur.

## Coach

Un coach avec qui discuter de stratégie. C'est Claude, l'IA d'Anthropic : il consulte tes données avant de
répondre (le plan de jeu suggéré, la liste des études, la stratégie du solveur à un nœud par famille de mains
avec équités et EV, une main précise, les écarts et les bluffs d'un adversaire réel) et explique pourquoi le
solveur choisit une action, en règles simples. Il lit aussi les plans et les études 6-max, avec les vraies
positions. Il sait faire lui-même un node-lock contre un adversaire réel, en heads-up (voir plus bas). Deux façons de lui parler :

### Dans ton abonnement Claude (sans clé API)

Analyzer donne ses outils à l'application Claude (Claude Desktop) ou à Claude Code par un serveur MCP local : la
conversation tourne dans ton abonnement, sans coût par question (dans les limites d'usage de ton abonnement).

```bash
python -m analyzer mcp --config
```

affiche, avec les chemins de ta machine :

- pour **Claude Desktop** : le bloc à coller dans `claude_desktop_config.json` (Réglages > Développeur >
  Modifier la configuration), puis redémarre l'application ;
- pour **Claude Code** : la commande `claude mcp add …` à lancer une fois.

Ensuite, pose tes questions **dans Claude** (pas dans le panneau du coach d'Analyzer, qui passe toujours par
l'API et sa facturation à part) : « Résume-moi le plan de jeu en SRP » ou « Dans quelles lignes Villain
bluffe-t-il ? ». Depuis l'explorateur, *Copier pour Claude* (onglet Coach) copie ta question avec ce que tu
regardes (spot ou main, ligne, case sélectionnée) : colle-la dans Claude, qui ouvre le même moment du coup.

Pour vérifier le branchement : dans Claude Code, `claude mcp list` doit montrer `analyzer` connecté (et `/mcp`
dans une session) ; dans Claude Desktop, le serveur apparaît dans Réglages > Développeur après un redémarrage
complet de l'application.
Le prompt *coach* du serveur (menu des prompts de l'application) donne au modèle la façon de répondre du coach.
Le serveur (`python -m analyzer mcp`) est lancé par l'application Claude : il charge tes mains au premier outil
appelé et ouvre les études comme l'application d'Analyzer (les deux peuvent tourner en même temps, chacune avec sa
propre session du solveur). Il n'y a pas de dépendance à installer.

### Dans l'application d'Analyzer (clé API)

Le même coach est intégré à l'application : onglet *Plan de jeu suggéré* (« Discuter avec le coach ») et
explorateur (onglet *Coach* à droite, qui sait quel spot, quelle ligne et quelle case tu regardes).

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...      # Windows : setx ANTHROPIC_API_KEY sk-ant-...
python -m analyzer app
```

- Clé API à créer sur console.anthropic.com (ou `ant auth login`) ; sans elle, le panneau explique quoi faire.
- Modèle : le plus récent des modèles de la famille Opus (les plus capables) que ta clé peut utiliser, choisi
  dans la liste des modèles de l'API ; effort `high`. À changer avec `ANALYZER_COACH_MODEL` et
  `ANALYZER_COACH_EFFORT`. Si le modèle décline une question, l'API la confie à un autre modèle Claude
  (`fallbacks: "default"`).
- Coût : facturé par Anthropic à l'usage (API séparée de l'abonnement), de 5 à 20 centimes par question environ
  selon les outils consultés ; la page affiche le coût estimé de la discussion. Le compte API doit avoir du
  crédit (console.anthropic.com, rubrique Billing) : sans crédit, le service refuse les questions (erreur 400).
- En cas d'erreur, le panneau donne la raison renvoyée par le service (et le terminal, le numéro de la requête).
  Si le relais vers un autre modèle n'est pas ouvert à ton compte, le coach s'en passe.
- Ouvrir une étude pour répondre prend quelques secondes (une minute en SRP) ; elle reste ouverte pour
  l'explorateur.

### Exploiter un adversaire réel (node-lock)

Demande au coach « Comment exploiter Villain sur ce flop ? » (ou, dans l'explorateur, « Comment exploiter mon
adversaire ici ? ») : il verrouille lui-même le profil de l'adversaire dans l'étude et lit la meilleure réponse.

1. **Son profil**, mesuré sur tes mains contre lui, dans ce type de pot et ce rôle (à l'initiative ou face à
   elle) : c-bet, 2e et 3e barrels, c-bet retardée, fold face à la c-bet et aux barrels, relances, fold et
   sur-relance face à une relance, probe (ou mise quand l'agresseur checke). Chaque fréquence est comparée à
   celle du solveur dans les mêmes situations (moyenne des plans de jeu des flops résolus de ce type de pot).
2. **L'écart devient un rapport de cotes**, ramené vers la théorie quand l'échantillon est petit : il compte pour
   moitié à 30 occasions, pas du tout sous 8, et moins quand le repère du solveur tient sur moins de 5 flops
   résolus (c'est le cas des pots 3bet tant qu'un seul flop est résolu).
3. **Le verrou** (`native/profil.rs`) : à chaque nœud de l'adversaire, la fréquence du solveur est déplacée de
   ce rapport de cotes, par un multiplicateur commun à toutes les mains. Il mise donc plus (ou moins) partout,
   mais toujours davantage là où le solveur mise déjà beaucoup ; l'ordre des mains et le partage entre les
   tailles sont gardés, et les mains jouées pures le restent. Les situations sans mesure gardent le jeu du
   solveur.
4. **La meilleure réponse** (`exploit_view` de GTOpen) au nœud demandé : le jeu du solveur et le jeu
   exploitant pour toute la range et par famille de mains, les mains qui changent d'action, l'EV et le gain en
   bb. Si c'est à l'adversaire d'agir, sa stratégie une fois verrouillée.

« récréatifs » ou « réguliers » à la place d'un pseudo donnent le profil moyen d'un groupe (plus de mains).
La meilleure réponse est maximale : elle suppose que l'adversaire ne s'adapte pas et pousse chaque main vers une
action pure. Le coach en tire une direction (quelles familles changent d'action, où est le gain) et conseille
une version tempérée. Compter de 5 à 40 secondes par nœud : ouverture de l'étude, verrou (de 1 à 5 s, 20 s pour
la plus grosse étude), meilleure réponse ; le verrou demande de la mémoire en plus (de 70 Mo à 1 Go selon
l'étude).

## Entraîneur

Menu **Entraîneur** de l'application : tu joues des mains sur les spots d'étude résolus (SRP, pots 3bet et
4bet), le solveur juge chaque décision.

- **Réglages** : type de pot ; flop précis, texture ou flop au hasard parmi ceux résolus (avec le nombre de
  mains avant de changer : changer de flop rouvre une étude, quelques secondes) ; ton côté (BB, BTN ou les
  deux) ; le départ : tout le coup dès le flop, la turn, la river, ou **une situation précise** (face à la
  c-bet, 2e barrel, probe river…, mêmes situations que pour le choix des tailles).
- **Une main** : on tire une ligne jusqu'au départ selon les fréquences du solveur (en grisé dans le
  déroulé), puis les deux mains ensemble dans les ranges du solveur à ce moment du coup. Tu joues ; l'adversaire
  joue la stratégie du solveur *pour sa main* ; turn et river tombent au hasard. Touches : 1, 2, 3… pour les
  actions, Espace ou Entrée pour continuer, R pour les réglages.
- **Après chaque décision** : ce que le solveur fait avec ta main (fréquences et EV de chaque action), la
  stratégie de toute la range, l'EV perdue, et un lien vers l'explorateur à ce nœud. Mêmes règles que
  « Face au solveur » : une action jouée au moins 10 % du temps avec ta main ne coûte rien ; une erreur, c'est
  plus de 0,25 bb d'EV perdue. Par défaut, on enchaîne sans attendre quand l'action est juste.
- **Tes progrès** : chaque décision est gardée (dans la base) ; la page de
  réglages montre, situation par situation, ton taux de décisions justes et l'EV perdue (en tout et sur
  7 jours), les plus coûteuses en tête, avec **S'entraîner** pour les retravailler.

Deux raccourcis : **S'entraîner ici** dans l'explorateur rejoue des mains à partir du nœud affiché (aussi sur
une main jouée recalculée), et **S'entraîner** dans « Les erreurs récurrentes » de *Face au solveur* lance la
situation où tu perds de l'EV.

## Sauvegarde

Tout ce qu'Analyzer garde est dans la base de données (`~/.analyzer/analyzer.db`, voir plus bas), sauf les arbres
des études (`~/.analyzer/etudes`) ; `ANALYZER_HOME` change de dossier. Menu **Sauvegarde** de l'application, ou en
ligne de commande :

```bash
python -m analyzer sauvegarde "C:\Users\toi\OneDrive\Analyzer"   # dossier synchronisé ; retenu ensuite
python -m analyzer sauvegarde gdrive:Analyzer --etudes                # stockage rclone, avec les études
python -m analyzer sauvegarde                                         # même destination que la dernière fois
python -m analyzer sauvegarde --auto        # sauvegarde automatique après chaque calcul dans l'application
python -m analyzer sauvegarde --restaurer   # sur un autre ordinateur : reprend la dernière sauvegarde
```

- **Destination** : un dossier, de préférence synchronisé en ligne (OneDrive, Google Drive, Dropbox : leur
  application l'envoie sur leurs serveurs), ou un stockage en ligne configuré avec
  [rclone](https://rclone.org) (`rclone config` une fois, puis `nom:dossier` : Google Drive, OneDrive, S3,
  SFTP…). Aucun mot de passe n'est gardé par Analyzer.
- **L'essentiel** (quelques Mo) : la base de données, copiée de façon cohérente même application ouverte (tes
  mains et celles de tes élèves, type des adversaires, résumés des mains analysées, tailles de mise choisies — les
  plus longues à recalculer : une heure et demie par flop SRP —, plans de jeu, résolutions, ranges, réglages,
  journal de l'entraîneur, fiches des études). Une archive datée par sauvegarde dans `archives/`, les 10 dernières
  gardées.
- **Les études** (`--etudes`, option de la page) : les fichiers des arbres résolus (20 Mo à quelques
  centaines de Mo chacun) dans `etudes/` ; seuls les nouveaux ou modifiés sont copiés ensuite.
- **Automatique** : après une résolution, un choix de tailles ou une main analysée, au plus une fois par
  quart d'heure.
- **Restaurer** : la base de la dernière archive est **fusionnée** dans celle de l'ordinateur : ce qui manque est
  ajouté, ce qui est plus récent dans l'archive remplace l'ancien, rien n'est effacé ; puis les arbres d'études
  absents. Une archive plus ancienne, faite de fichiers (d'avant la base), est reprise de la même façon.

Les tailles choisies et les synthèses de référence peuvent aussi rejoindre le dépôt (fichiers
`analyzer/theory/data/*_tailles.json` et `*_reference.json`, voir plus haut) : elles sont alors livrées avec
Analyzer.

### Base de données

Tout ce qu'Analyzer garde est dans une base de données : tes mains et celles de tes élèves (avec le texte d'origine
de chaque historique), le type de tes adversaires, les résumés des mains passées au solveur, et les données du
solveur — tailles de mise choisies, plans de jeu, résultats des résolutions et précision de chaque spot, solutions
préflop des tables à plusieurs et ranges ajustées, réglages et durées des résolutions, journal de l'entraîneur,
fiches des études :

- **Sur ton ordinateur** : SQLite, `~/.analyzer/analyzer.db` (rien à installer). Elle est créée au premier
  lancement et reprend, une seule fois, ce qui était dans des fichiers (`joueurs.json`, `revue/`, les élèves de
  `eleves/` et leurs historiques, `tailles/`, `plans/`, `resolutions/`, `ranges/`, `entrainement/`,
  `reglages.json`, `precisions.json`, `durees.json`, les fiches `etudes/*.json`) ; ces fichiers restent en place
  mais ne servent plus (tu peux les effacer, sauf les arbres `etudes/*.etude`).
- **En ligne** : PostgreSQL, avec la même structure. `pip install "psycopg[binary]"` puis
  `ANALYZER_DB=postgresql://utilisateur:motdepasse@hôte:5432/base` avant de lancer l'application.
- **Organisation** : un compte (« local » ici, un client en ligne), ses espaces (toi, chaque élève), et dans chaque
  espace ses historiques (texte d'origine compressé, importé une seule fois) et ses mains (une seule fois chacune,
  même si deux historiques la contiennent). Le type des adversaires, les résumés et les données du solveur
  appartiennent au compte. Si le code de lecture des historiques change, les mains sont relues depuis le texte
  gardé.
- **À jour partout** : chaque écriture d'un type de données (tailles, ranges, études…) change sa révision dans la
  base ; les calculs qui en dépendent (spots d'étude à jour, clés des spots des mains) se refont alors, même quand
  l'écriture vient d'un autre programme (la ligne de commande pendant que l'application tourne, un autre serveur).
- **Évolutions** : le schéma est versionné (`analyzer/db/schema.py`) ; les nouvelles versions s'appliquent seules
  à l'ouverture.
- **Sauvegarde** : la base est l'archive (copie cohérente, même application ouverte) ; la restauration la fusionne
  dans celle de l'ordinateur (`analyzer/db/merge.py`). Une base PostgreSQL est sauvegardée par son hébergeur.
- **Les arbres des études** (fichiers `.etude`, jusqu'à quelques centaines de Mo) restent des fichiers, derrière
  une interface de stockage (`analyzer/blobs.py`) : un dossier sur ton ordinateur ; en ligne, un stockage objet
  (S3 ou compatible, hébergé en UE) prendra le relais sans toucher au reste, le dossier local servant de cache au
  solveur.

```bash
python -m analyzer base                                   # état : espaces, mains, historiques, analyses
python -m analyzer base --importer ~/Historiques          # importe un dossier (et ses .zip) dans ton espace
python -m analyzer base --importer ~/Paul --eleve paul    # … dans l'espace d'un élève
python -m analyzer base --copier-vers postgresql://moi:motdepasse@hote:5432/analyzer   # vers une base vide
python -m analyzer base --fusionner-depuis sqlite:///chemin/analyzer.db   # ajoute ce qui manque d'une autre base
python -m analyzer base --fusionner-depuis sqlite:///… --compte client-1  # … dans un compte précis
```

Ordre de grandeur : 8 000 mains s'importent en 4 secondes et se rechargent en moins d'une seconde ; la base
pèse environ 0,9 Ko par main, historique d'origine compris.

### Cache des calculs

Les calculs longs et toujours identiques sont gardés dans `~/.analyzer/cache/analyses.sqlite` : les mains lues
(par fichier d'historique : un fichier inchangé ne se relit pas), les équités (abattages, tapis préflop) et les clés
du spot postflop de chaque main (pour savoir si elle est déjà analysée sans reconstruire son arbre). Ce cache n'est
pas sauvegardé : il se reconstruit tout seul, et on peut l'effacer sans rien perdre. `ANALYZER_CACHE=0` le coupe.
Ordre de grandeur (4 cœurs) : les douze pages de *Mon jeu* et d'un adversaire se calculent en 1 seconde environ
avec 793 mains (au lieu de 45) et en 6 à 7 secondes avec 8 000 mains, puis restent en mémoire.

Les pages restent légères quel que soit le nombre de mains : dans l'application, *Spots* ne contient plus les
mains (le serveur filtre, trie et envoie la liste par pages de 150, puis le détail d'une main quand on l'ouvre :
50 Ko au lieu de 9 Mo pour 8 000 mains) ; la courbe de résultats garde la forme de la série avec au plus
1 500 points ; les tableaux de mains montrées du rapport s'arrêtent aux 40 plus récentes (toutes dans *Spots*).
Les fichiers autonomes (`python -m analyzer`, rapport téléchargé) gardent toutes les mains dans la page.

## Contenu du rapport

| Section | Ce qu'on y trouve |
|---|---|
| Plan de jeu | Son profil en une phrase et au plus 4 consignes par moment du coup (préflop, quand tu mises, face à ses mises, à tester), chacune avec sa preuve chiffrée et un niveau de confiance |
| Résultat | Ton gain en bb et en €, bb/100, résultat **EV all-in** (la part de chance), gains avec/sans abattage, courbe main par main (une case par courbe dans la légende pour la masquer ; l'échelle suit les courbes affichées, le choix est gardé) |
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
  parsers/             lecture des historiques : betclic.py, winamax.py, unibet.py
  ring.py              tables à 3 joueurs et plus : tes stats par position, tes adversaires et leurs fréquences
  models.py            modèle commun (Hand, Action, Seat)
  stats.py             lecture des situations HU et agrégation des stats
  cards.py             notation des mains, évaluateur 7 cartes, équité
  insights.py          repères, exploits, duel, tells de sizing
  lines.py             lignes value / bluff, folds forcés, pertes sans abattage
  bluffs.py            où un adversaire bluffe : fréquences par carte face au solveur, mains montrées, patterns
  plan.py              plan de jeu généré à partir de l'analyse
  spots.py             fiches des mains pour le visualiseur (tags de spot, lignes, équités)
  report.py            rapport HTML
  viewer.py            visualiseur de spots (HTML + JavaScript, sans dépendance)
  selfreport.py        « Mon jeu » : ton bilan contre tous tes adversaires
  theory/              préflop vs solveur : solution (data/), comparaison (preflop.py), page (page.py),
                       arbre préflop pour l'explorateur (preflop_tree.py), plan de jeu suggéré
                       (coach.py), profil d'un adversaire réel et node-lock (exploit.py),
                       catégories de mains pour les filtres (handclass.py),
                       lecture de captures de ranges (extract.py) ; postflop avec GTOpen : spots,
                       installation et lecture des résultats (postflop.py), pont Rust (native/main.rs ;
                       arbre.rs pour les tailles, profil.rs pour le verrou d'un profil d'adversaire),
                       commande `gtopen` (solve_cli.py), mains jouées face au solveur (review.py),
                       ranges des tables à plusieurs (ring_ranges.py), ton préflop face aux charts
                       (ring_preflop.py), arbre préflop 6-max pour l'explorateur (ring_tree.py), tes ranges ajustées
                       (custom_ranges.py, ordre des mains dans data/hand_order.json)
  app/                 application : serveur local (server.py), bibliothèque de mains et cache
                       (library.py), résolutions et sessions du solveur (solves.py), page « Face au
                       solveur » (review_page.py), leakfinding (leaks_page.py), bluffs des adversaires
                       (bluffs_page.py), étude du field (field_page.py), bilan des tables à plusieurs
                       (bilan_page.py), mains de départ (hands_page.py), entraîneur
                       (trainer.py, static/trainer.*), plan de jeu suggéré (plan_page.py), coach
                       (coach_chat.py, static/coach.*) et son serveur MCP pour l'abonnement Claude
                       (mcp_server.py), sauvegardes en arrière-plan (backups.py), interface
                       (static/, dont l'explorateur explorer.*)
  backup.py            sauvegarde et restauration de la base et des études (commande `sauvegarde`)
  blobs.py             stockage des gros fichiers (arbres des études) : un dossier ici, un stockage objet en ligne
  store.py             cache sur disque des calculs longs (SQLite : mains lues, équités, clés des spots)
  players.py           type des adversaires : régulier ou récréatif (choix et suggestion)
  leaks.py             leakfinding : stats face à la théorie, revue du solveur, mains à revoir, leaks prioritaires
  ring_leaks.py        leakfinding aux tables à plusieurs : réguliers et récréatifs, préflop face aux charts, après le
                       flop face aux plans 6-max
  handplay.py          mains de départ : résultat de chaque main, décisions préflop face au fold et à la théorie,
                       d'où vient la perte (suite du coup, main au flop, fin du coup, solveur)
  students.py          les élèves : un espace de la base par élève
  db/                  base de données : connexion SQLite ou PostgreSQL et schéma versionné (__init__.py,
                       schema.py), historiques et mains (hands.py), résumés du solveur (analyses.py), documents
                       du solveur et révisions (documents.py), fiches des études (studies.py), journal de
                       l'entraîneur (training.py), reprise des anciens fichiers (legacy.py), fusion de deux bases
                       (merge.py), commande `base` (cli.py)
  cli.py               ligne de commande
tests/                 tests unitaires (python -m unittest)
hands/                 tes historiques (ignorés par git)
reports/               rapports générés (ignorés par git)
```

### Sites et formats de table

| Site | Héros | Particularités |
|---|---|---|
| Betclic | étiquette `[Hero]` | heure de chaque action (temps de réflexion) |
| Winamax | ligne « Dealt to » | pot du résumé net du rake ; aux tables anonymes, « Incognito 2 » devient « Incognito-<identifiant> » (ligne *Player Info*) pour ne pas mélanger deux joueurs assis à la même place |
| Unibet | compte entre crochets (`Pseudo[Unibet_…]`) | joueurs « sitting out » écartés (pas servis) |

Ton pseudo peut changer d'un site à l'autre : le héros de chaque main prend le pseudo principal (celui qui revient
le plus souvent), pour que *Mon jeu* réunisse tous les sites. Chaque joueur reçoit sa position (BTN, SB, BB, CO,
HJ, UTG…) d'après le bouton et les blindes postées. L'import indique, par fichier, le site et le nombre de mains
en HU, 3-max, 6-max ou à 7-9 joueurs. Les mains heads-up (deux joueurs servis) ont toute l'analyse ; celles des
tables de 3 joueurs et plus sont réunies sous *Tables à plusieurs*.

**Tables à plusieurs** (onglet de *Mon jeu*, et de chaque élève) : tes stats par position, toutes tes tables de 3 à
9 joueurs ensemble (une position compte ses mains de chaque taille de table), sur toutes tes mains, contre les
réguliers et contre les récréatifs (comme en heads-up) — mains et bb/100, VPIP, PFR, open quand tu parles le
premier, limp, 3bet et call face à une ouverture, fold face au 3bet après ton open, défense des blindes face à un
vol (ouverture du CO, du bouton ou de la SB sans caller), c-bet au flop en pot à deux ou à plusieurs, fold face à
la c-bet, abattage et gain à l'abattage. Des repères indicatifs d'un régulier 6-max à 100 bb (stats de tracker
courantes) colorent les écarts. En tête de page, **tes écarts les plus importants** contre les réguliers face aux
charts (avec les mêmes cartes) et aux plans de jeu des flops 6-max résolus, le détail étant dans le Leakfinding.
Code : `analyzer/ring.py` (lecture des mains), `analyzer/ring_leaks.py` (face à la théorie) et `app/ring_page.py`
(la page).

**Pots à deux joueurs au flop** (3-max, 6-max) : quand il ne reste que deux joueurs au flop, le coup se résout au
solveur postflop comme un coup heads-up — hors de position celui qui parle le premier après le flop, pot avec
l'argent mort des joueurs qui ont foldé, tapis effectif des deux joueurs. Leurs ranges viennent de **ta solution
préflop** du format (une table sans solution à elle prend les charts 6-max à même nombre de joueurs derrière : le
3-max ceux du BTN, de la SB et de la BB, une table de 7 à 9 joueurs le LJ comme l'UTG), un fichier JSON à importer
dans la base (`python -m analyzer ranges --importer 6-max.json`,
sauvegardé ; son format est lu dans le fichier, sinon dans son nom, ou donné avec `--format`) :

```json
{"format": "6-max", "stack_bb": 100, "source": "…",
 "lines": {"CO:raise BB:call": {"pot_type": "SRP", "ranges": {"CO": "AA,AKs,KQo:0.5,…", "BB": "…"}},
           "BTN:raise SB:raise BTN:call": {"pot_type": "pot 3bet", "ranges": {"SB": "…", "BTN": "…"}}}}
```

Une ligne = les relances et calls préflop dans l'ordre, avec la position de leur auteur (les folds n'y sont pas) ;
chaque range est celle du joueur au flop (« main » ou « main:poids »).

En 6-max, les charts gratuits de [Hand2Note Guide](https://hand2noteguide.com/fr/poker/free-poker-tools/preflop-gto-charts/)
(100 bb, tirés de PioSolver, fréquences arrondies à 25 %) se chargent d'un clic dans l'onglet *Tables à plusieurs*,
ou avec `python -m analyzer ranges --hand2note`. Ils donnent l'open de chaque position, la réponse à un open et la
réponse de l'ouvreur au 3bet : on en tire, en 6-max, 8 pots simples (open, call), 15 pots 3bet (open, 3bet, call ;
l'ouvreur garde les mains qu'il ouvre *et* paie le 3bet) et 15 pots 4bet (open, 3bet, 4bet hors tapis, call). Le
site ne publiant pas la réponse au 4bet, celle du 3bettor vient d'une réponse type mesurée sur une capture de solveur
(la SB face au 4bet du bouton, `theory/data/vs4bet_reference.json`), appliquée à toutes les positions. Le **3-max**
reprend les charts du BTN, de la SB et de la BB (8 lignes). Les conditions d'utilisation du site réservent ces charts
à un usage personnel : ils sont téléchargés pour toi, dans ta base, jamais dans le dépôt. `python -m analyzer ranges` liste les solutions présentes. L'onglet *Tables à plusieurs* liste ces
coups (les plus gros pots d'abord) avec *Ouvrir au solveur ↗*, ou la raison s'ils ne se résolvent pas encore :
pas de range pour la ligne, troisième joueur qui a mis de l'argent avant de se coucher (call puis fold, squeeze),
tapis préflop. L'explorateur montre le préflop de toute la table et les vraies positions (`theory/ring_ranges.py`).

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
demandent ni Rust ni GTOpen. Chaque test qui touche la base a son propre dossier temporaire : rien n'est écrit
dans `~/.analyzer`. Les tests de la base tournent aussi sur PostgreSQL avec une base **dédiée aux tests, effacée à
chaque test** :

```bash
ANALYZER_TEST_PG=postgresql://analyzer@127.0.0.1:5432/analyzer_test python -m unittest tests.test_db
```

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
