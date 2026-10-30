# Procedure CAST : Makefile classique et SDK Linux

Cette variante locale prepare un package exploratoire a partir d'un Makefile
classique, de sources C et d'en-tetes applicatifs, DDS et SDK disponibles.
Elle utilise `makefile_sdk_recover.py` et un profil JSON relu par un operateur.
Elle ne necessite ni CI, ni build, ni execution de Make ou du compilateur.

Les flux existants CMake/Conan restent disponibles via
`makefile_local_recover.py` et `cast_offline_collector.py`.

## 1. Assembler la racine locale

Le script lit un repertoire materialise, jamais une archive ou un listing texte.
Assembler les fichiers recus dans la racine d'application, par exemple :

```text
APPLICATION_Archive_CAST/
  Makefile
  src/*.c
  include/*.h
  build_tools/
    cross_build.mk
    cflags/cflags.mk
  DDS/rti_connext_dds-7.3.1/include/
  env_cross_compil/bbel/3.0/
    environment-setup-aarch64-knds-linux
    site-config-aarch64-knds-linux
    version-aarch64-knds-linux
    sysroots/aarch64-knds-linux/usr/include/
```

Pour les archives examinees, `build_tools.zip` contient directement
`cross_build.mk` et `cflags/` : les placer sous `build_tools/`.
`3.0.zip` contient directement les trois fichiers de configuration du SDK :
les placer sous `env_cross_compil/bbel/3.0/`. Les en-tetes du sysroot et de DDS
proviennent de l'arborescence originale, pas de ces deux petites archives.

L'absence des scripts `environment-setup.d/*.sh` reste une reserve ; elle
n'empeche pas cette preparation exploratoire. Le script n'invente aucun probe
compilateur et ne suppose pas que les scripts etaient absents lors du build.

## 2. Relire le profil

`examples/makefile-sdk-linux-arm64.json` encode le contexte examine : GCC croise
AArch64, `-mbranch-protection=standard`, les options de `cflags.mk`, les macros
explicites et les includes DDS. Il doit etre adapte pour tout autre contexte.

Le profil est une description explicite du contexte choisi. Le script ne parse
pas les conditions Make ni les scripts shell, et ne verifie pas automatiquement
que le contenu des fichiers de preuve correspond aux valeurs du profil.
Toute modification de cible, de Makefile, de SDK ou de surcharge de variables
doit donc conduire a une nouvelle revue du profil.

| Champ | Base et usage |
|---|---|
| `compiler`, `architecture` | Driver sans arguments et architecture cible |
| `flags` | Liste d'arguments, sans chemins, macros ni choix de sources |
| `defines` | Macros explicites, dans l'ordre retenu |
| `application_roots` | Repertoires relatifs a `--root` a copier sous `source/manual/` |
| `source_globs` | Selection C relative a `--root`, par exemple `src/*.c`, non recursive |
| `includes` | Racines et sous-repertoires d'inclusion ordonnes, categorie `I` ou `isystem` |
| `includes[].dependency` | Nom de destination sous `dependencies/`, si dependance externe |
| `sysroot.path` | Sysroot cible relatif a `--root` |
| `sysroot.include_subdirectories` | Sous-repertoires d'en-tetes a recopier dans `dependencies/sdk/` |
| `evidence_files` | Fichiers recus conserves sous `provenance/` pour la revue |

Les `CFLAGS` du Makefile remplacant ceux du SDK sont deja pris en compte dans
l'exemple. Le `--sysroot` porte par `CC` est conserve comme chemin relatif.
Les includes implicites supplementaires du compilateur, notamment ses en-tetes
internes GCC, ne sont pas deduits de `usr/include` et restent a verifier.

## 3. Produire le package

Depuis le depot, sous Windows PowerShell :

```powershell
python makefile_sdk_recover.py --root "C:\reception\APPLICATION_Archive_CAST" --profile examples/makefile-sdk-linux-arm64.json --output "C:\work\APPLICATION_CAST_exploratory"
```

Sous Linux :

```sh
python3 makefile_sdk_recover.py \
  --root /reception/APPLICATION_Archive_CAST \
  --profile examples/makefile-sdk-linux-arm64.json \
  --output /work/APPLICATION_CAST_exploratory
```

Le repertoire de sortie et son ZIP doivent etre nouveaux et exterieurs a la
racine scannee. Le ZIP est cree a cote du repertoire, avec le suffixe `.zip`.

Le script affiche immediatement un message de demarrage, puis la progression du
scan, des copies, du controle de couverture et de la creation du ZIP. Ces messages
sont envoyes sur stderr avec vidage immediat ; le resume JSON final reste sur stdout.
Dans un outil qui capture les flux separement, afficher egalement stderr.

Code `0` : preparation exploratoire terminee et ZIP cree. Ce code ne signifie
pas `READY_FOR_ANALYSIS` en strict. Code `2` : erreur ou entree requise manquante ;
consulter le rapport si le repertoire de diagnostic a ete cree. Aucun ZIP n'est
produit en presence de probleme de copie, d'include manquant ou de preuve absente.

## 4. Controler les livrables

- `recovery-report.json` : statut `EXPLORATORY`, origine reconstruite, reserves,
  anomalies et sources copiees sans commande reconstruite.
- `compilation/compile_commands.json` : commandes reconstruites avec chemins
  relatifs a la racine du package (`directory = .`). Il s'agit d'une trace portable,
  pas de commandes observees ni d'une base directement utilisable pour compiler.
- `copy-mapping.json` : correspondance des chemins sous `--root` vers `--output`.
- `copy-diagnostics.json` et `copy-coverage.json` : fichiers exclus et raisons,
  bilan des fichiers d'entree, sans assimiler les metadonnees generees a des copies.
- `provenance/` : profil utilise et copies des fichiers de configuration recus.
  Ces preuves peuvent conserver des chemins historiques absolus ; les chemins
  operationnels dans les commandes et la configuration CAST sont relatifs.
- `cast-config/` : reference de profil, configuration de l'AU automatique,
  includes, macros explicites candidates et aide pour les modules.

Les sources et en-tetes des racines applicatives sont copies, y compris les
sources hors du glob de compilation ; ces dernieres sont signalees dans le rapport.
Les dependances ne contribuent que leurs en-tetes et fichiers associes autorises,
y compris les en-tetes texte sans extension. Les binaires, documentation du SDK,
sources de debug du SDK et fichiers `.d` ne sont pas copies pour analyse.

## 5. Analyse CAST exploratoire

Importer directement le ZIP produit dans Imaging, conserver l'AU C/C++ auto-creee
et appliquer manuellement les references de `cast-config/single-analysis-unit.json`.
Les includes applicatifs/DDS sont ordonnes ; les includes SDK sont des candidats
avec ordre implicite non verifie. Le force-include des seules macros explicites
reste desactive (`disabled_unverified_compiler_context`) en attendant la revue.

Examiner les erreurs de preprocesseur, headers manquants et erreurs de parsing
apres l'analyse. Les modules sont crees apres l'analyse complete avec
`MODULE_CREATION_HELP.md` et `module-candidates.json` comme aides a revoir.

Ce package autonome n'est pas une entree qualifiee du collecteur strict Conan/QCC.
Il ne fabrique ni inventaire Conan, ni probes QCC. La qualification stricte requiert
des elements complementaires fiables et un flux adapte, pas un changement manuel
de statut ou la suppression de controles dans le collecteur existant.
