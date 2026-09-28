# Analyzer

Outil d'analyse de tes adversaires en **Heads-Up NLHE** à partir de tes historiques de mains.
Il lit les historiques, calcule le profil complet de l'adversaire (et le tien dans le même
match), détecte ses tendances exploitables et génère un **rapport HTML** autonome.

- Python 3.10+, **aucune dépendance** à installer.
- Sites supportés : **Betclic.fr** (cash game HU). D'autres formats peuvent être ajoutés (voir plus bas).

## Utilisation

1. Dépose tes fichiers d'historique dans `hands/` (un ou plusieurs fichiers `.txt`, sous-dossiers acceptés).
   Les mains en double sont ignorées : tu peux accumuler les sessions contre un même joueur.
2. Lance :

```bash
python -m analyzer                       # un rapport par adversaire (30 mains minimum)
python -m analyzer --liste               # liste les adversaires trouvés
python -m analyzer -a berserk            # un seul adversaire (nom partiel, accents ignorés)
python -m analyzer ~/Downloads/Hand.txt -a "peste noire" -o rapports/
```

Ton pseudo est détecté automatiquement (tag `Hero` de Betclic) ; sinon passe `--hero TonPseudo`.
Le terminal affiche un résumé et le rapport complet est écrit dans `reports/<adversaire>.html`.

## Contenu du rapport

| Section | Ce qu'on y trouve |
|---|---|
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
  report.py            rapport HTML
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
