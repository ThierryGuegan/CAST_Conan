# Clôture de l’audit P0/P1/P2

## P0 — critères bloquants

| Exigence | Mise en œuvre | Preuve |
|---|---|---|
| Modes strict/exploratoire et code retour | `Audit.status()`, retour `2`, pas de ZIP strict en échec | tests strict et exploratoire |
| Inventaire Conan exact | `packages.json`, identité RREV/PREV/package ID/contexte, confrontation au graphe | test de doublon et test référence binaire exacte |
| Identité de build | `BUILD_IDENTITY.json`, version et champs obligatoires, `BUILD_IDENTITY.txt` automatique | test nominal et export bout en bout |
| Intégrité du transfert | `FILES.sha256` exhaustif, rejet absent/modifié/non listé/doublon | contrôle avant collecte |
| Profils par compilation | signature par langage/options/includes/macros/sysroot/variante | test de deux jeux de macros |
| Base de compilation obligatoire | `compile_commands.json` ou `compilation-units.json` | échec strict si absent/vide |
| Response files | expansion récursive, limite, cycle/absence/ambiguïté bloquants | tests présent et absent |
| QCC | inventaire variant/langage, macros et includes implicites | test nominal QNX |
| Liens symboliques | rejet à l’entrée et copies sans suivi | test symlink |
| Secrets | détection, blocage et copie redacted des preuves textuelles | test secret |
| Couverture des sources | 100 % des sources C/C++ du périmètre | test source non représentée |
| Aucun chemin non résolu | includes, sources, force-includes et imacros bloquants | `unresolved-paths.txt` |

## P1 — fidélité de compilation

| Exigence | Mise en œuvre |
|---|---|
| `.d` robustes | continuations, échappement shell, règles phony `-MP` ignorées |
| `-include`/`-imacros` | remapping, contrôle d’existence, force-include par profil |
| Sysroot par commande | `--sysroot`, `-isysroot`, chemins `=...` |
| Préprocesseur transmis | `-Wp,...`, `-Xpreprocessor` |
| QNX | miroir d’en-têtes, probe par variant/langage, macros fonctionnelles incluses |
| En-têtes atypiques | extensions C/C++ et fichiers sans extension |
| Conan | seuls les packages `host` alimentent l’analyse ; `build` reste une preuve |
| Plan CAST | `analysis-units.json`, `CAST_ANALYSIS_PLAN.md`, includes relatifs au package |
| Manuel/généré | unités séparées, même lorsqu’un profil est commun |

## P2 — exploitation et suivi

| Exigence | Mise en œuvre |
|---|---|
| Qualification des logs CAST | compteurs header/préprocesseur/parseur/symboles ; erreurs bloquantes |
| Baseline | empreintes comparées pour identité, Conan et profils |
| Statut machine | `collection-status.json` |
| Dérive et métriques | `baseline-diff.json`, couverture et `volumetry.json` |
| Tests | 17 tests unitaires/intégration, sans dépendance externe |
| Procédure client | export CI, inventaire Conan 2, probes QCC, identité et hash automatisés |

## Condition de mise en référence

La version est techniquement prête pour un pilote lorsque les 17 tests passent. La mise en référence opérationnelle reste conditionnée à un test sur un build réel de chacune des applications A et B, pour chaque cible retenue, puis à la revue des logs de la première analyse CAST.
