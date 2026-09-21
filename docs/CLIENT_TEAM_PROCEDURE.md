# Procédure client — production du bundle `CAST_EVIDENCE_BUNDLE`

Version de procédure : 1.0 — collecteur v2.1.0

Cette procédure s’applique aux applications A et B, à leurs variantes Linux/QNX/x86/ARM et à toute configuration réellement remise à l’équipe CAST. Elle est exécutée par l’équipe client dans la CI/Build Factory, dans le même environnement que le build qualifié. L’équipe CAST ne lance ni Conan, ni Make, ni GCC/QCC.

## 1. Objectif et résultat attendu

À la fin du job, la CI doit déposer un répertoire autonome :

```text
CAST_EVIDENCE_BUNDLE/
```

Ce répertoire permet à CAST de reconstruire les unités d’analyse et leurs chemins sans accès au cache Conan, au QNX SDP ou à la machine de build.

Le job client doit produire un bundle par combinaison :

```text
application × commit × cible × architecture × build_type × profil Conan
```

Ne pas mélanger A et B, ni deux cibles, ni deux variantes QCC dans un même bundle.

## 2. Responsabilités

| Activité | Équipe client | Équipe CAST |
|---|---:|---:|
| Build normal et tests produit | Oui | Non |
| Résolution Conan et accès au cache | Oui | Non |
| Production du graphe, package IDs et chemins | Oui | Non |
| Production de `compile_commands.json` | Oui | Non |
| Export des en-têtes Conan/QNX | Oui, automatisé | Non |
| Génération de `BUILD_IDENTITY.json` et `FILES.sha256` | Oui, automatisée | Vérification |
| Exécution du collecteur | Non | Oui |
| Configuration CAST Imaging | Non | Oui |
| Correction d’un bundle rejeté | Oui | Diagnostic |

## 3. Règles impératives

1. Exécuter l’export après un build réussi, dans le même job et la même image de toolchain.
2. Ne jamais reconstruire pour CAST avec des options différentes du produit livré.
3. Ne jamais laisser l’équipe CAST accéder au cache Conan, au QNX SDP, aux secrets CI ou au réseau de build.
4. Produire un bundle séparé par cible et par application.
5. Ne pas modifier le bundle après la génération de `FILES.sha256`.
6. Ne pas remplacer un chemin Conan par une supposition basée sur le nom du dossier.
7. Conserver le job, le commit, le graphe Conan et le bundle comme un même ensemble de preuve.

## 4. Préparer les entrées du job

Avant de lancer l’exporteur, vérifier que le job dispose de :

```text
SRC_ROOT/                         sources manuelles C/C++/Python et headers
GENERATED_ROOT/                   code Matlab généré, si séparé
BUILD_ROOT/                       compile_commands.json, .d, .rsp et journaux
CONAN_GRAPH_JSON                  graphe Conan résolu du build
CONAN_LOCKFILE(S)                 lockfile réellement utilisé
CONAN_PROFILE(S)                  profils host et build réellement utilisés
QNX_TARGET                        racine du SDK QNX de la toolchain
QCC_PROBE_DIR                     probes macros/includes par variante et langage
```

Le dossier source doit être limité au périmètre de l’application et de la cible. Si le dépôt contient plusieurs applications ou plusieurs produits, filtrer avant l’export afin que la vérification de couverture à 100 % soit significative.

## 5. Produire `compile_commands.json`

Le fichier doit contenir une entrée par source réellement compilée, avec au minimum :

```json
[
  {
    "directory": "/ci/work/Application-A",
    "file": "/ci/work/Application-A/src/main.c",
    "arguments": ["qcc", "-Vgcc_ntoaarch64le", "-I/...", "-DPRODUCT=1", "-c", "/ci/work/Application-A/src/main.c"]
  }
]
```

Les champs `arguments` sont préférés à `command`, car ils évitent une nouvelle interprétation shell. Si seule la forme `command` existe, conserver la commande complète telle qu’exécutée.

Contrôles client :

- le fichier est un tableau JSON non vide ;
- chaque `file` existe dans le workspace de build ;
- les chemins `-include` et `-imacros` existent ;
- les response files `@xxx.rsp` sont archivés ;
- les commandes contiennent le compilateur et, pour QCC, `-V<variante>` ;
- aucune entrée ne pointe vers une ancienne version de source ou de cache.

Si le build utilise déjà un `compile_commands.json`, le réutiliser. Il ne faut pas relancer une compilation uniquement pour CAST.

## 6. Produire les probes GCC/QCC

Pour chaque couple réellement utilisé `(variante, langage)`, produire deux fichiers dans `QCC_PROBE_DIR`.

Exemple QCC C :

```sh
printf '' | qcc -Vgcc_ntoaarch64le -x c -dM -E - \
  > "$QCC_PROBE_DIR/gcc_ntoaarch64le-c.macros.txt"

printf '' | qcc -Vgcc_ntoaarch64le -x c -E -v - \
  > /dev/null \
  2> "$QCC_PROBE_DIR/gcc_ntoaarch64le-c.includes.txt"
```

Exemple QCC C++ :

```sh
printf '' | qcc -Vgcc_ntoaarch64le -x c++ -dM -E - \
  > "$QCC_PROBE_DIR/gcc_ntoaarch64le-cxx.macros.txt"

printf '' | qcc -Vgcc_ntoaarch64le -x c++ -E -v - \
  > /dev/null \
  2> "$QCC_PROBE_DIR/gcc_ntoaarch64le-cxx.includes.txt"
```

Créer ensuite `qcc-variants.json` :

```json
{
  "schema_version": 1,
  "variants": [
    {
      "variant": "gcc_ntoaarch64le",
      "language": "c",
      "macros_file": "compiler/gcc_ntoaarch64le-c.macros.txt",
      "include_search_file": "compiler/gcc_ntoaarch64le-c.includes.txt"
    },
    {
      "variant": "gcc_ntoaarch64le",
      "language": "c++",
      "macros_file": "compiler/gcc_ntoaarch64le-cxx.macros.txt",
      "include_search_file": "compiler/gcc_ntoaarch64le-cxx.includes.txt"
    }
  ]
}
```

Pour GCC, appliquer le même principe si des headers implicites ou des macros toolchain sont nécessaires. Le probe doit être produit avec la même image, le même sysroot et la même variante que le build.

## 7. Produire l’inventaire Conan exact

### Conan 2

Depuis le répertoire du consumer et avec les profils réellement utilisés :

```sh
conan graph info . \
  -pr:h "$HOST_PROFILE" \
  -pr:b "$BUILD_PROFILE" \
  --format=json > "$BUILD_ROOT/cast/conan-graph.json"

python3 conan2_inventory.py \
  --graph "$BUILD_ROOT/cast/conan-graph.json" \
  --output "$BUILD_ROOT/cast/packages.pre-export.json"
```

Le script appelle `conan cache path` pour chaque nœud binaire et enregistre :

```text
reference
recipe_revision
context = host | build
package_id
package_revision
original_root
logical_root
```

### Conan 1 ou format de graphe interne

Si la version Conan utilisée ne produit pas le format attendu, le job doit générer le même JSON à partir de ses sorties structurées. Le fichier doit toujours contenir une ligne par identité exacte. Une recherche heuristique par nom de répertoire est interdite.

Exemple minimal :

```json
{
  "schema_version": 1,
  "packages": [
    {
      "reference": "middleware/4.2@company/stable",
      "recipe_revision": "rrev-sha",
      "context": "host",
      "package_id": "package-id-sha",
      "package_revision": "prev-sha",
      "original_root": "/conan-cache/p/middleware-package",
      "logical_root": "conan://middleware/4.2/package-id-sha",
      "exported_root": ""
    }
  ]
}
```

Règles :

- `host` : headers nécessaires à l’analyse ; `original_root` obligatoire ;
- `build` : traçabilité uniquement ; ses headers ne sont pas injectés ;
- ne pas dédupliquer deux package IDs différents ;
- inclure RREV et PREV lorsqu’ils existent ;
- conserver le graphe brut et le lockfile dans le bundle.

## 8. Collecter les headers Conan et QNX

L’exporteur client copie automatiquement :

- les headers Conan `host` vers `conan/export/<package>` ;
- les headers QNX depuis `QNX_TARGET` vers `qnx/` ;
- les headers sans extension s’ils sont textuels ;
- les liens symboliques internes en fichiers matérialisés ;
- aucun lien sortant de la racine n’est accepté.

Ne pas copier tout le cache Conan. Ne pas copier les packages `build` dans les dépendances d’analyse. Les bibliothèques binaires ne sont pas nécessaires pour l’analyse statique des sources et peuvent contenir des données inutiles ou sensibles.

## 9. Collecter les journaux et dépendances

Le job doit conserver :

```text
BUILD_ROOT/*.d
BUILD_ROOT/*.rsp
BUILD_ROOT/compile_commands.json
BUILD_LOG
CONAN_GRAPH_JSON
CONAN_LOCKFILE(S)
CONAN_PROFILE(S)
```

Les journaux sont expurgés automatiquement par `client_ci_export.py` pour les motifs de type `token=`, `password=`, Bearer et clés privées. Le collecteur CAST effectue un second scan. Un secret détecté côté CAST bloque le mode strict.

Les fichiers `.d` servent de preuve complémentaire. Ils ne remplacent pas la compilation database : ils ne suffisent pas à reproduire l’ordre des includes, les macros et les options QCC.

## 10. Générer automatiquement l’identité

Lancer `client_ci_export.py` avec les métadonnées du build :

```sh
python3 client_ci_export.py \
  --bundle "$ARTIFACT_DIR/CAST_EVIDENCE_BUNDLE" \
  --source "$SRC_ROOT" \
  --generated "$GENERATED_ROOT" \
  --build-root "$BUILD_ROOT" \
  --compile-commands "$BUILD_ROOT/compile_commands.json" \
  --conan-packages "$BUILD_ROOT/cast/packages.pre-export.json" \
  --conan-graph "$BUILD_ROOT/cast/conan-graph.json" \
  --qnx-target "$QNX_TARGET" \
  --qcc-probe-dir "$QCC_PROBE_DIR" \
  --application Application-A \
  --application-version "$APPLICATION_VERSION" \
  --target-label "$TARGET_LABEL" \
  --target-os QNX \
  --target-os-version "$QNX_VERSION" \
  --architecture "$TARGET_ARCH" \
  --build-type "$BUILD_TYPE" \
  --compiler qcc \
  --compiler-version "$QCC_VERSION" \
  --compiler-variant "$COMPILER_VARIANT" \
  --conan-version "$CONAN_VERSION" \
  --host-profile "$HOST_PROFILE" \
  --build-profile "$BUILD_PROFILE" \
  --source-root-at-build "$SRC_ROOT" \
  --generated-root-at-build "$GENERATED_ROOT" \
  --qnx-target-at-build "$QNX_TARGET" \
  --matlab-version "$MATLAB_VERSION" \
  --generation-id "$MODEL_BUILD_ID"
```

Le script lit automatiquement `CI_COMMIT_SHA`, `GITHUB_SHA` ou `BUILD_SOURCEVERSION` pour le commit, ainsi que les identifiants de pipeline/job connus. Dans un environnement différent, fournir explicitement `--git-commit`, `--pipeline-id` et `--job-id`.

Le fichier `identity/BUILD_IDENTITY.json` est créé par le script. Le fichier `BUILD_IDENTITY.txt` est ensuite produit par le collecteur CAST dans le package d’analyse.

## 11. Générer et contrôler `FILES.sha256`

`client_ci_export.py` génère automatiquement `FILES.sha256` après toutes les copies. Le job doit ensuite :

1. vérifier que le fichier existe ;
2. vérifier que le nombre de fichiers est cohérent avec le contenu attendu ;
3. archiver le dossier sans modification ;
4. transférer le bundle et, si la politique l’exige, une signature externe ;
5. conserver le checksum et l’identifiant du pipeline.

Ne pas ouvrir, reformater ou réordonner le bundle après la génération du manifeste.

## 12. Exemple de job CI complet

```sh
set -eu

python3 conan2_inventory.py \
  --graph "$BUILD_ROOT/cast/conan-graph.json" \
  --output "$BUILD_ROOT/cast/packages.pre-export.json"

python3 client_ci_export.py \
  --bundle "$ARTIFACT_DIR/CAST_EVIDENCE_BUNDLE" \
  --source "$CI_PROJECT_DIR/src" \
  --generated "$CI_PROJECT_DIR/generated" \
  --build-root "$BUILD_ROOT" \
  --compile-commands "$BUILD_ROOT/compile_commands.json" \
  --build-log "$BUILD_ROOT/build.log" \
  --conan-packages "$BUILD_ROOT/cast/packages.pre-export.json" \
  --conan-graph "$BUILD_ROOT/cast/conan-graph.json" \
  --conan-lockfile "$CI_PROJECT_DIR/conan.lock" \
  --conan-profile "$HOST_PROFILE_FILE" \
  --qnx-target "$QNX_TARGET" \
  --qcc-probe-dir "$BUILD_ROOT/cast/compiler" \
  --application "$APPLICATION" \
  --target-label "$TARGET_LABEL" \
  --target-os "$TARGET_OS" \
  --architecture "$TARGET_ARCH" \
  --build-type "$BUILD_TYPE" \
  --compiler "$COMPILER" \
  --compiler-variant "$COMPILER_VARIANT" \
  --host-profile "$HOST_PROFILE" \
  --build-profile "$BUILD_PROFILE" \
  --source-root-at-build "$CI_PROJECT_DIR/src"
```

Le job doit échouer si l’une des deux commandes retourne un code différent de zéro.

## 13. Checklist avant transfert

- [ ] Le build de l’application est terminé avec succès.
- [ ] Le bundle correspond à une seule application et une seule cible.
- [ ] `BUILD_IDENTITY.json` contient commit, pipeline, cible, OS, architecture, compilateur et profils Conan.
- [ ] `compile_commands.json` est présent et non vide.
- [ ] Toutes les response files référencées sont présentes.
- [ ] Les probes QCC couvrent chaque variante/langage utilisé.
- [ ] `conan-graph.json`, `packages.json`, profils et lockfiles sont présents.
- [ ] Les headers Conan `host` et QNX sont copiés.
- [ ] Les `.d` et journaux sont présents si disponibles.
- [ ] Aucun secret n’est présent dans les journaux transférés.
- [ ] `FILES.sha256` est le dernier fichier généré.
- [ ] Le répertoire est archivé sans modification.

## 14. Diagnostic d’un rejet CAST

| Code de rejet | Cause probable | Correction côté client |
|---|---|---|
| `IDENTITY-*` | métadonnée manquante ou incohérente | compléter les options d’identité et régénérer |
| `MANIFEST-*` | fichier ajouté/modifié après hash | recréer entièrement le bundle |
| `CONAN-GRAPH-MISMATCH` | package ID/RREV/PREV incorrect | régénérer l’inventaire depuis le graphe exact |
| `CONAN-EXPORT-MISSING` | cache indisponible ou chemin relatif incorrect | relancer l’export dans l’environnement Conan |
| `QCC-PROBE-*` | variante ou fichier de probe manquant | produire le probe dans la même toolchain |
| `RESPONSE-*` | `.rsp` absent, ambigu ou cyclique | archiver le fichier référencé et vérifier le chemin |
| `INCLUDE-UNRESOLVED` | `-I`, sysroot ou header hors bundle | ajouter le miroir correspondant, sans modifier la commande |
| `SOURCE-COVERAGE-INCOMPLETE` | source hors compilation database | exporter le bon périmètre ou corriger la preuve du build |
| `SEC-SECRET` | secret dans une preuve | expurger à la source et régénérer le manifeste |

Ne pas corriger les chemins à la main dans le package CAST. Toute correction doit être faite dans le job client puis suivie d’un nouvel export complet.

## 15. Transfert à CAST

Transmettre à CAST :

1. l’archive du `CAST_EVIDENCE_BUNDLE` ;
2. le checksum et, si requis, la signature ;
3. l’identifiant du commit et du pipeline ;
4. l’application, la cible et le profil Conan ;
5. le contact client capable de régénérer le bundle.

L’équipe CAST exécute ensuite uniquement :

```sh
python3 cast_offline_collector.py \
  --input-root /reception/CAST_EVIDENCE_BUNDLE \
  --output-parent /work/cast-packages \
  --mode strict
```

Le statut attendu avant configuration Imaging est `READY_FOR_ANALYSIS`. Tout statut `NOT_QUALIFIED` doit revenir à l’équipe client pour correction et régénération.
