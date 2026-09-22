# Commandes locales — variante makefiles modifiés

Ce fichier donne un exemple de séquence locale. Il n’y a pas d’intégration CI dans cette variante.

## 1. Lancer le build local avec les makefiles modifiés

Adapter la cible `make` au dépôt client :

```sh
make clean
make <CIBLE_PRODUIT> <OPTIONS_DU_BUILD>
```

Les makefiles modifiés doivent produire un staging CAST local, par exemple :

```text
build/cast/staging/
```

Un ZIP limité au dossier `build/` ne remplace pas ce staging. Il peut être audité, mais il ne doit pas être transmis comme `CAST_DELIVERABLES_BUNDLE`.

## 2. Normaliser le staging en bundle

```sh
python3 makefile_local_export.py \
  --staged-root "$PWD/build/cast/staging" \
  --bundle "$PWD/build/cast/CAST_DELIVERABLES_BUNDLE" \
  --application "<APPLICATION>" \
  --application-version "<APPLICATION_VERSION>" \
  --git-commit "<GIT_COMMIT>" \
  --run-id "local-<DATE>-<OPERATEUR>" \
  --target-label "<TARGET_LABEL>" \
  --target-os "<TARGET_OS>" \
  --target-os-version "<TARGET_OS_VERSION>" \
  --architecture "<TARGET_ARCH>" \
  --build-type "<BUILD_TYPE>" \
  --compiler "<COMPILER>" \
  --compiler-version "<COMPILER_VERSION>" \
  --compiler-variant "<COMPILER_VARIANT>" \
  --conan-version "<CONAN_VERSION>" \
  --host-profile "<HOST_PROFILE>" \
  --build-profile "<BUILD_PROFILE>" \
  --source-root-at-build "$PWD/src" \
  --generated-root-at-build "$PWD/generated" \
  --qnx-target-at-build "<SDK_ROOT>"
```

Si le staging contient déjà `identity/BUILD_IDENTITY.json`, le script le conserve. Ajouter `--force-identity` pour régénérer l’identité depuis les arguments locaux.

## 3. Archiver le bundle localement

Exemple :

```sh
tar -czf CAST_DELIVERABLES_BUNDLE.tgz -C "$PWD/build/cast" CAST_DELIVERABLES_BUNDLE
```

Transmettre l’archive, la fiche de contexte et le commit des makefiles modifiés à l’équipe CAST.

## 4. Auditer une archive brute reçue

```sh
python3 makefile_local_audit.py --root /local/LCCS_Archive_CAST
```

Un statut `DIAGNOSTIC_ONLY` indique que le fichier contient des traces exploitables pour comprendre le build, mais pas les livrables nécessaires à une collecte stricte.

## 5. Récupération partielle si le staging complet est indisponible

```sh
python3 makefile_local_recover.py \
  --root /local/LCCS_Archive_CAST \
  --output recovered-from-root
```

Pour limiter la récupération à une application :

```sh
python3 makefile_local_recover.py \
  --root /local/LCCS_Archive_CAST \
  --application <NOM_APPLICATION> \
  --output recovered-from-root
```

Cette sortie ne remplace pas automatiquement `CAST_DELIVERABLES_BUNDLE`. Elle sert à inventorier les répertoires `build` présents sous la racine pour toutes les applications détectées, puis à reconstruire les commandes et l’inventaire partiel lorsque les fichiers CMake/Conan sont disponibles.
