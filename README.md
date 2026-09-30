# Analyzer

Outil d'analyse de tes adversaires en **Heads-Up NLHE** à partir de tes historiques de mains.
Il lit les historiques, calcule le profil complet de l'adversaire (et le tien dans le même
match), détecte ses tendances exploitables et génère un **rapport HTML** autonome, ainsi qu'un
**visualiseur de spots** pour filtrer et rejouer les coups toi-même.

- Python 3.10+, **aucune dépendance** à installer.
- Sites supportés : **Betclic.fr** (cash game HU). D'autres formats peuvent être ajoutés (voir plus bas).

## Application

```bash
python -m analyzer app
```

L'application s'ouvre dans ton navigateur (http://127.0.0.1:8765). Tout reste sur ton ordinateur :
le serveur n'écoute qu'en local et refuse les requêtes venant d'autres sites.

- **Menu latéral** : « Mon jeu », « Importer des mains » et la liste de tes adversaires (recherche,
  nombre de mains, ton résultat contre chacun).
- **Adversaire** : onglets *Plan de jeu*, *Rapport* et *Spots*. Les liens « voir les mains » du rapport
  ouvrent directement l'onglet Spots sur la bonne ligne.
- **Mon jeu** : ton bilan contre tous tes adversaires (résultats, courbe, écarts aux repères, stats,
  pertes sans abattage, résultats par adversaire) et *Mes spots* sur toutes tes mains.
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
Le terminal affiche un résumé ; le rapport complet est écrit dans `reports/<adversaire>.html` et le
visualiseur dans `reports/<adversaire>-spots.html` (ouvre-les dans ton navigateur, ils fonctionnent hors ligne).

## Comment ça marche

1. **Lecture** (`parsers/`) : chaque historique est découpé en mains, converties dans un format commun
   (positions, tapis, actions avec montants, board, cartes montrées, gains, rake). Chaque pot est vérifié :
   mises − montants non payés = pot total, et gains + rake = pot total.
2. **Lecture des situations** (`stats.py`) : pour chaque main, chaque décision est rangée dans sa situation
   (« BB face à un open », « agresseur qui peut c-bet le flop », « face à un check-raise »…). Une stat vaut
   toujours « fois où il l'a fait / fois où il pouvait le faire ». Les deux joueurs sont analysés de la même façon.
3. **Lecture de l'adversaire** (`insights.py`) : ses stats sont comparées à des repères de régulier HU ; un écart
   est « net » si l'intervalle de confiance à 90 % reste hors du repère, sinon « tendance ».
4. **Ses lignes** (`lines.py`) : chaque mise postflop est rangée par ligne (contexte et taille). À l'abattage,
   sa main est classée au moment de la mise (value, value fine, semi-bluff, bluff) grâce à un évaluateur
   de mains et à un calcul d'équité exact contre ta main. Ta main quand tu foldes est aussi notée.
5. **Plan de jeu** (`plan.py`) : des règles génériques transforment ces constats en consignes, chacune
   avec sa preuve et un niveau de confiance.
6. **Sorties** (`report.py`, `viewer.py`, `selfreport.py`) : le rapport HTML, le visualiseur de spots,
   le bilan « Mon jeu » et le résumé du terminal.
7. **Application** (`app/`) : un serveur local sert ces pages dans une interface avec menu et onglets,
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
- **Repères** : ordres de grandeur pour un régulier HU solide à 100bb+. Ils servent à repérer les écarts, pas à définir une stratégie optimale. Un écart est marqué **net** quand l'intervalle de confiance à 90 % est entièrement hors du repère, sinon **tendance**. Il faut au moins 15 occasions pour qu'une stat soit interprétée.
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
  app/                 application : serveur local (server.py), bibliothèque de mains et cache
                       (library.py), interface (static/)
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
```

## Plan de jeu

Le plan est produit par des règles génériques : chacune ne se déclenche que si les données la justifient
(par exemple « face à ses 3bets, défends plus » exige qu'il 3bet plus de 20 % et que tu foldes plus de 50 %).
Chaque consigne affiche sa preuve et un niveau de confiance :

- **solide** : l'écart reste vrai même en tenant compte du hasard (intervalle de confiance à 90 %) ;
- **indicatif** : tendance nette sur un échantillon modeste ;
- **à confirmer** : peu de mains, à vérifier sur les prochaines sessions.

La rubrique « À tester » liste ses lignes turn et river qu'on n'a presque jamais vues à l'abattage alors que
tu as souvent foldé une paire ou mieux : c'est là qu'un call de temps en temps apporte l'information qui manque.
Plus tu accumules de sessions contre un joueur dans `hands/`, plus le plan s'affine.
