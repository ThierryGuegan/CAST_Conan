# Procédure équipe CAST — exploitation d’un bundle de livrables

Version de procédure : 1.0 — procédure générique pour le collecteur CAST

Cette procédure décrit les actions réalisées par l’équipe CAST après réception d’un bundle de livrables produit par l’équipe client. Elle constitue le mode opératoire de production côté CAST.

L’équipe CAST ne lance ni Conan, ni l’outil de build, ni le compilateur, et ne reconstruit pas l’application. Elle exploite uniquement le bundle transféré, exécute le collecteur, configure CAST Imaging à partir du package produit et qualifie le résultat d’analyse.

## 1. Objectif

Transformer un bundle client autonome en package CAST portable, puis utiliser ce package pour configurer et qualifier une analyse CAST Imaging.

Le résultat attendu avant configuration Imaging est :

```text
collection-status.json => READY_FOR_ANALYSIS
```

Tout statut `NOT_QUALIFIED` doit être retourné à l’équipe client avec le diagnostic produit. Le package ne doit pas être corrigé manuellement côté CAST.

## 2. Responsabilités CAST

| Activité | Équipe CAST |
|---|---:|
| Réceptionner le bundle de livrables | Oui |
| Vérifier l’identité et le périmètre déclarés | Oui |
| Exécuter le collecteur CAST | Oui |
| Décider si le package est exploitable | Oui, à partir du statut et de la revue |
| Configurer CAST Imaging | Oui |
| Qualifier les logs d’analyse | Oui |
| Corriger le bundle client | Non |
| Relancer Conan, le build ou le compilateur | Non |

## 3. Entrées attendues

L’équipe CAST reçoit un répertoire ou une archive correspondant à un seul périmètre d’analyse :

```text
application × commit × cible × architecture × build_type × profil Conan × compilateur
```

Par convention, le répertoire d’entrée peut être nommé :

```text
CAST_DELIVERABLES_BUNDLE/
```

Le bundle doit contenir au minimum l’identité du build, les sources, la compilation database ou son équivalent, les journaux disponibles, les graphes/profils Conan, les en-têtes Conan `host`, les probes compilateur et les en-têtes SDK/toolchain nécessaires.

La procédure de production côté client est décrite dans `docs/CLIENT_TEAM_PROCEDURE.md`.

## 4. Préconditions CAST

Avant l’exécution :

1. confirmer que le bundle client a été produit après un build réussi ;
2. vérifier que le transfert est terminé ;
3. vérifier la signature ou le contrôle d’intégrité d’entreprise si le processus client en fournit un ;
4. disposer de Python 3.9+ ;
5. travailler dans un répertoire dédié ;
6. utiliser un compte sans privilège ;
7. prévoir un espace disque libre supérieur à deux fois la taille du bundle plus celle du package attendu ;
8. choisir un répertoire de sortie situé hors du bundle d’entrée.

Le processus doit être lancé avec un compte sans privilège, dans un répertoire de travail dédié. Le répertoire de sortie ne doit pas se trouver sous le bundle d’entrée.

## 5. Réception du bundle

À la réception, contrôler sans modifier le contenu :

1. le nom de l’application et de la cible ;
2. le commit, le pipeline, la version applicative et le profil Conan déclarés ;
3. l’absence de mélange entre plusieurs applications, cibles ou architectures ;
4. la présence de `identity/BUILD_IDENTITY.json` ;
5. la présence de `compilation/compile_commands.json` ou `compilation/compilation-units.json`.

Si le périmètre est ambigu, arrêter la procédure et demander un nouveau bundle.

## 6. Exécution du collecteur

Exécuter le collecteur en mode strict :

```sh
python3 cast_offline_collector.py \
  --input-root /reception/CAST_DELIVERABLES_BUNDLE \
  --output-parent /work/cast-packages \
  --mode strict
```

Pour comparer avec un package précédent :

```sh
python3 cast_offline_collector.py \
  --input-root /reception/CAST_DELIVERABLES_BUNDLE \
  --output-parent /work/cast-packages \
  --baseline /work/cast-packages/previous-package \
  --mode strict
```

Codes de sortie attendus :

| Code | Statut | Action CAST |
|---:|---|---|
| 0 | `READY_FOR_ANALYSIS` | Revoir le package et préparer CAST Imaging |
| 0 | `EXPLORATORY` | Utiliser uniquement pour diagnostic |
| 2 | `NOT_QUALIFIED` | Ne pas analyser ; retourner le diagnostic au client |
| 3 | `OPERATIONAL_ERROR` | Corriger l’environnement CAST puis relancer |

Le mode strict ne produit pas de package exploitable lorsqu’une anomalie critique est détectée.

## 7. Contrôles du package produit

Avant toute configuration CAST Imaging, vérifier les fichiers suivants dans le package produit :

| Fichier | Contrôle attendu |
|---|---|
| `collection-status.json` | statut `READY_FOR_ANALYSIS` |
| `collection-report.json` | absence d’anomalie critique |
| `cast-config/single-analysis-unit.json` | configuration de l’AU C/C++ créée automatiquement par Imaging |
| `cast-config/profile-reference.json` | référence des profils séparés, chemins relatifs au package |
| `cast-config/module-candidates.json` | candidats de modules à créer après analyse |
| `cast-config/MODULE_CREATION_HELP.md` | aide opérationnelle pour créer les modules |
| `cast-config/force-include-all.h` | force-include consolidé présent, utilisable seulement si activé dans `single-analysis-unit.json` |
| `CAST_ANALYSIS_PLAN.md` | plan lisible et cohérent avec le périmètre |
| `cast-config/path-remapping.csv` | chemins originaux remappés vers `source://`, `conan://` ou `qnx://` |
| `cast-config/source-coverage-summary.json` | couverture compatible avec le périmètre livré |
| `cast-config/baseline-diff.json` | dérive comprise et acceptable si une baseline existe |
| `volumetry.json` | volumétrie cohérente avec l’application attendue |
| `manifest.sha256` | intégrité interne du package produit par le collecteur |

Si `unresolved-paths.txt` contient des chemins non résolus, le package n’est pas exploitable en strict.

## 8. Configuration CAST Imaging

Utiliser `CAST_ANALYSIS_PLAN.md` comme consigne opérationnelle, puis appliquer manuellement les paramètres de `cast-config/single-analysis-unit.json` à l’Analysis Unit C/C++ créée automatiquement par CAST Imaging. Ce JSON est une fiche de configuration portable, pas un artefact d’import automatique.

Ne pas créer manuellement d’Analysis Units C/C++ ou JEE : ces technologies ne le supportent pas dans le flux Imaging actuel. `profile-reference.json` reste une référence de profils pour contrôle et diagnostic, pas une recette de création manuelle d’AU.

Pour l’AU C/C++ auto-créée :

1. sélectionner `source/manual` comme racine source, et `source/generated` si le répertoire existe ;
2. résoudre les chemins de `cast-config/cast-include-paths.relative.txt` relativement à la racine du package ;
3. conserver l’ordre des includes ;
4. utiliser le champ `force_include` de `cast-config/single-analysis-unit.json` seulement si `force_include_status = enabled` ;
5. ne pas ajouter de dossier Conan `build` ;
6. lancer l’analyse complète ;
7. créer les modules après analyse, depuis le code analysé, en utilisant `cast-config/MODULE_CREATION_HELP.md` et `cast-config/module-candidates.json` comme aide.

Si `force_include_status` commence par `disabled_`, ne pas appliquer `force-include-all.h` globalement. Les conflits listés dans `macro_conflicts` ou `preinclude_conflicts` doivent être traités comme un point de diagnostic après la première analyse complète.

Les modules doivent être créés uniquement après la première analyse complète, à partir des objets visibles dans CAST Imaging. Les candidats proposés par `module-candidates.json` sont un point de départ : ils peuvent être fusionnés ou découpés selon les résultats d’analyse, les dépendances réelles et les règles de gouvernance applicative.

Pour Python, configurer les répertoires indiqués dans le plan. Les sources Python ne dépendent pas des options GCC/QCC.

Les intitulés exacts des écrans peuvent varier selon la version de CAST Imaging. Le plan produit par le collecteur reste la référence de configuration.

## 9. Qualification après analyse

Après une première analyse CAST Imaging :

1. conserver les logs CAST ;
2. placer les logs dans `cast-analysis-logs/` du bundle ou d’une copie de travail dédiée ;
3. relancer le collecteur sur ce périmètre ;
4. vérifier `qualification.json` ;
5. contrôler les erreurs de préprocesseur, headers manquants, parseurs et symboles inconnus ;
6. documenter la décision de qualification.

Un résultat sans alerte automatique ne remplace pas la revue humaine. Si les logs montrent des erreurs structurelles, retourner le diagnostic au client pour régénération du bundle.

## 10. Première mise en service

Pour une première application ou une nouvelle cible :

1. exécuter un pilote sur une première application et une première cible ;
2. comparer les unités d’analyse aux commandes de compilation d’un échantillon manuel ;
3. lancer CAST Imaging et fournir les logs dans `cast-analysis-logs/` ;
4. relancer le collecteur sur le même périmètre ;
5. obtenir une qualification sans erreur automatique ;
6. répéter sur chaque application et chaque cible réellement analysée ;
7. geler la version du collecteur, les schémas et la baseline de référence.

La baseline ne doit pas être créée depuis un package `EXPLORATORY`.

## 11. Diagnostic et retour client

En cas de `NOT_QUALIFIED`, transmettre au client :

1. le statut exact ;
2. les codes de rejet du rapport ;
3. les chemins non résolus utiles au diagnostic ;
4. la cible, le commit et le profil Conan concernés ;
5. la commande de collecte utilisée ;
6. la demande de régénération complète du bundle.

Ne pas corriger à la main :

- `compile_commands.json` ;
- les chemins Conan ;
- les chemins SDK/toolchain ;
- les probes compilateur ;
- les sources copiées ;
- les fichiers de configuration générés par le collecteur.

Toute correction doit être faite côté client dans le job d’export, puis livrée dans un nouveau bundle.

## 12. Incident et rollback

En cas d’incident :

1. conserver le bundle d’entrée, le package produit, les rapports et les logs ;
2. reproduire dans un répertoire isolé ;
3. utiliser `--mode exploratory` uniquement pour enrichir le diagnostic ;
4. comparer avec la baseline ou le package précédent si disponible ;
5. revenir au tag précédent du collecteur si une régression est confirmée ;
6. régénérer entièrement le package après correction.

Un package produit ne doit jamais devenir une nouvelle source manuelle de vérité. La source de vérité reste le bundle client et la version du collecteur utilisée.

## 13. Livrables CAST

À la fin du traitement, conserver :

| Livrable | Usage |
|---|---|
| package CAST produit | support de configuration Imaging |
| `collection-status.json` | verdict machine |
| `collection-report.json` | diagnostic de collecte |
| `CAST_ANALYSIS_PLAN.md` | plan de configuration |
| `single-analysis-unit.json` | configuration de l’AU C/C++ auto-créée |
| `profile-reference.json` | référence des profils |
| `module-candidates.json` | candidats de modules post-analyse |
| `MODULE_CREATION_HELP.md` | aide de création des modules |
| `force-include-all.h` | force-include consolidé, si activé |
| `qualification.json` | qualification après analyse, si logs fournis |
| logs CAST Imaging | trace d’exécution et support de diagnostic |

Le bundle client, le package produit et les logs doivent rester associés au même commit, au même pipeline et au même périmètre d’analyse.
