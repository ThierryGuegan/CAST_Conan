# Kit client — production du bundle de livrables CAST

Ce kit est destiné à l’équipe client qui doit produire `CAST_DELIVERABLES_BUNDLE` dans sa CI après un build applicatif réussi.

Le kit ne demande pas de relancer Conan, le build ou le compilateur pour CAST. Il s’intègre dans le job qui possède déjà le workspace source, le répertoire de build, le cache Conan résolu, les profils, le SDK ou sysroot éventuel, les journaux et les résultats de compilation.

## 1. Contenu à transmettre au client

Transmettre les éléments suivants ensemble :

```text
docs/CLIENT_TEAM_PROCEDURE.md
client-kit/README.md
client-kit/CLIENT_CONTEXT_FORM.md
client-kit/client-inputs.env.example
client-kit/gitlab-ci.export.example.yml
client_ci_export.py
conan2_inventory.py
schemas/
examples/
```

`cast_offline_collector.py` n’est pas requis côté client si l’exécution du collecteur reste côté CAST.

## 2. Rôle des fichiers

| Fichier ou dossier | Usage côté client |
|---|---|
| `docs/CLIENT_TEAM_PROCEDURE.md` | procédure détaillée de production du bundle |
| `client-kit/README.md` | mode d’emploi court du kit |
| `client-kit/CLIENT_CONTEXT_FORM.md` | fiche de contexte à remplir lors du premier transfert ou d’un changement de périmètre |
| `client-kit/client-inputs.env.example` | liste des variables à adapter au contexte client |
| `client-kit/gitlab-ci.export.example.yml` | exemple de job CI post-build |
| `client_ci_export.py` | exporte les sources, journaux, compilation database, headers Conan et SDK/toolchain |
| `conan2_inventory.py` | produit l’inventaire exact des packages Conan 2 depuis le graphe JSON |
| `schemas/` | formats JSON attendus |
| `examples/` | exemples de fichiers d’entrée |

## 3. Préconditions

Le job d’export doit disposer de :

1. Python 3.9 ou plus ;
2. un build applicatif déjà réussi ;
3. `compile_commands.json` produit pendant ce build, ou `compilation-units.json` équivalent ;
4. le graphe Conan résolu du build, si Conan est utilisé ;
5. les profils et lockfiles Conan réellement utilisés ;
6. les probes compilateur pour chaque variante et langage réellement utilisés ;
7. les journaux, `.d` et `.rsp` disponibles ;
8. les variables d’identité applicative et cible.

Le job doit produire un bundle par application, cible, architecture, type de build, profil Conan et variante compilateur.

## 4. Préparer les variables

Copier `client-kit/client-inputs.env.example` dans la CI ou dans le dépôt client, puis renseigner les valeurs propres au projet.

Les variables minimales à contrôler sont :

```text
APPLICATION
TARGET_LABEL
TARGET_OS
TARGET_ARCH
BUILD_TYPE
COMPILER
COMPILER_VARIANT
HOST_PROFILE
BUILD_PROFILE
SRC_ROOT
BUILD_ROOT
CONAN_GRAPH_JSON
CONAN_PACKAGES_JSON
COMPILER_PROBE_DIR
```

Les variables liées au SDK, au code généré, à la version applicative ou aux journaux peuvent rester vides si elles ne s’appliquent pas.

## 5. Produire l’inventaire Conan 2

Si le projet utilise Conan 2, produire d’abord le graphe dans l’environnement du build :

```sh
conan graph info . \
  -pr:h "$HOST_PROFILE_FILE" \
  -pr:b "$BUILD_PROFILE_FILE" \
  --format=json > "$CONAN_GRAPH_JSON"
```

Puis produire l’inventaire exact :

```sh
python3 conan2_inventory.py \
  --graph "$CONAN_GRAPH_JSON" \
  --output "$CONAN_PACKAGES_JSON"
```

Le script appelle `conan cache path` pour résoudre les dossiers réels du cache. Il ne déduit pas les packages depuis les noms de dossiers.

Si le projet utilise Conan 1 ou un mécanisme interne, produire un fichier équivalent au format `schemas/conan-packages.schema.json`. Voir `examples/packages.pre-export.json`.

## 6. Produire les probes compilateur

Créer un fichier de variantes compilateur et les fichiers macros/includes associés.

Pour QCC, par exemple :

```sh
printf '' | qcc -Vgcc_ntoaarch64le -x c -dM -E - \
  > "$COMPILER_PROBE_DIR/gcc_ntoaarch64le-c.macros.txt"

printf '' | qcc -Vgcc_ntoaarch64le -x c -E -v - \
  > /dev/null \
  2> "$COMPILER_PROBE_DIR/gcc_ntoaarch64le-c.includes.txt"
```

Adapter la commande au compilateur réellement utilisé. Le probe doit provenir de la même image de toolchain, du même sysroot et de la même variante que le build livré.

## 7. Lancer l’export client

Commande type :

```sh
python3 client_ci_export.py \
  --bundle "$ARTIFACT_DIR/CAST_DELIVERABLES_BUNDLE" \
  --source "$SRC_ROOT" \
  --generated "$GENERATED_ROOT" \
  --build-root "$BUILD_ROOT" \
  --compile-commands "$COMPILE_COMMANDS_JSON" \
  --build-log "$BUILD_LOG" \
  --conan-packages "$CONAN_PACKAGES_JSON" \
  --conan-graph "$CONAN_GRAPH_JSON" \
  --conan-lockfile "$CONAN_LOCKFILE" \
  --conan-profile "$HOST_PROFILE_FILE" \
  --qnx-target "$SDK_ROOT" \
  --qcc-probe-dir "$COMPILER_PROBE_DIR" \
  --application "$APPLICATION" \
  --application-version "$APPLICATION_VERSION" \
  --target-label "$TARGET_LABEL" \
  --target-os "$TARGET_OS" \
  --target-os-version "$TARGET_OS_VERSION" \
  --architecture "$TARGET_ARCH" \
  --build-type "$BUILD_TYPE" \
  --compiler "$COMPILER" \
  --compiler-version "$COMPILER_VERSION" \
  --compiler-variant "$COMPILER_VARIANT" \
  --conan-version "$CONAN_VERSION" \
  --host-profile "$HOST_PROFILE" \
  --build-profile "$BUILD_PROFILE" \
  --source-root-at-build "$SRC_ROOT" \
  --generated-root-at-build "$GENERATED_ROOT" \
  --qnx-target-at-build "$SDK_ROOT" \
  --matlab-version "$CODE_GENERATOR_VERSION" \
  --generation-id "$CODE_GENERATION_ID"
```

Ne passer `--generated`, `--build-log`, `--conan-lockfile`, `--conan-profile`, `--qnx-target` et `--qcc-probe-dir` que lorsque les fichiers ou dossiers existent dans le contexte client.

Le script lit automatiquement les variables CI courantes pour le commit, le pipeline et le job lorsqu’elles existent. Sinon, fournir `--git-commit`, `--pipeline-id` et `--job-id`.

## 8. Contrôler avant transfert

Avant d’archiver et de transmettre le bundle, vérifier :

- `identity/BUILD_IDENTITY.json` est présent ;
- `compilation/compile_commands.json` est présent et non vide ;
- `conan/packages.json` et `conan/conan-graph.json` sont présents si Conan est utilisé ;
- les profils et lockfiles réellement utilisés sont présents ;
- les headers Conan `host` nécessaires sont copiés ;
- les probes compilateur couvrent les variantes et langages réellement utilisés ;
- les `.rsp` référencés par les commandes de compilation sont présents ;
- les journaux et `.d` disponibles sont inclus ;
- le bundle correspond à une seule application et une seule cible.

## 9. Transmettre à CAST

Transmettre :

1. l’archive de `CAST_DELIVERABLES_BUNDLE` ;
2. le commit et l’identifiant de pipeline ;
3. l’application, la cible, l’architecture et le type de build ;
4. le profil Conan host/build et la variante compilateur ;
5. le contact client capable de régénérer le bundle ;
6. la fiche `client-kit/CLIENT_CONTEXT_FORM.md` remplie lors du premier transfert ou lorsqu’un périmètre change.

L’équipe CAST exécute ensuite le collecteur hors ligne sur le bundle transmis.
