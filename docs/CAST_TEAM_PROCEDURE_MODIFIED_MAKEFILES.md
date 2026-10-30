# Procédure équipe CAST — variante avec makefiles modifiés

Cette procédure complète `docs/CAST_TEAM_PROCEDURE.md` pour le cas où l’équipe client fournit un bundle issu de makefiles modifiés.

Le collecteur CAST reste le même : l’équipe CAST exécute `cast_offline_collector.py` sur `CAST_DELIVERABLES_BUNDLE`. La différence porte sur l’origine du bundle côté client.

## 1. Points à vérifier à la réception

En plus des contrôles de la procédure CAST standard, vérifier :

1. le commit ou tag contenant les makefiles modifiés ;
2. la confirmation que le staging CAST provient du build local qualifié ;
3. la présence d’un contact capable de corriger les makefiles et de relancer un build ;
4. la fiche `client-kit-modified-makefiles/CLIENT_CONTEXT_FORM_MODIFIED_MAKEFILES.md`.

Si l’équipe CAST reçoit une arborescence locale incomplète, elle ne doit pas être traitée comme un bundle. Lancer d’abord :

```sh
python3 makefile_local_audit.py --root /reception/PROJECT_Archive_CAST
```

Un statut `DIAGNOSTIC_ONLY` signifie que l’arborescence contient des traces utiles pour expliquer le build, mais qu’elle ne contient pas tous les livrables nécessaires. Si le client ne peut pas régénérer un staging complet, utiliser un flux de récupération partielle basé sur le contenu réel du répertoire racine :

```sh
python3 makefile_local_recover.py \
  --root /reception/PROJECT_Archive_CAST \
  --output /work/recovered-from-root
```

Ce flux exploite les répertoires `build` présents sous la racine pour toutes les applications détectées. Pour limiter le traitement à un module, ajouter `--application <NOM_APPLICATION>`. Il est utile pour reconstruire un `compile_commands.json` partiel, rattacher les en-têtes Conan host identifiés par les métadonnées Conan disponibles et récupérer les probes compilateur présentes sous `compiler/`.

Si aucune probe compilateur n’est livrée, `makefile_local_recover.py` reconstruit les fichiers QCC à partir des commandes CMake récupérées : `qcc-variants.json` depuis les options `-V`, les macros depuis `-D/-U` et les chemins d’inclusion depuis `-I`/`-isystem`. Le rapport marque ces fichiers `reconstructed_from_compile_commands` ; ils permettent de poursuivre l’analyse locale, mais l’équipe CAST doit les valider avant de déclarer le bundle strict. Les fichiers `.d` ne sont pas recopiés par cette récupération locale : ils ne sont utiles qu’en traçabilité complémentaire et ne doivent pas réintroduire des chemins absolus dans la sortie.

La configuration CAST Imaging ne doit pas être créée à la main à partir de l’arborescence brute. Elle doit s’appuyer sur le package produit par `cast_offline_collector.py` lorsque le bundle est qualifié : `cast-config/single-analysis-unit.json`, `cast-config/cast-include-paths.relative.txt`, `cast-config/force-include-all.h`, `cast-config/profile-reference.json`, `cast-config/module-candidates.json`, `cast-config/MODULE_CREATION_HELP.md`, `cast-config/compilation-profiles.json`, `cast-config/dependency-files.csv` et `CAST_ANALYSIS_PLAN.md`.

Ne pas créer manuellement d’Analysis Units C/C++ ou JEE dans Imaging. Utiliser l’AU C/C++ créée automatiquement, reporter manuellement les paramètres de `single-analysis-unit.json`, appliquer le `force_include` global uniquement lorsque `force_include_status = enabled`, lancer l’analyse complète, puis créer les modules après analyse avec `MODULE_CREATION_HELP.md` et `module-candidates.json`. `profile-reference.json` reste une référence de profils pour diagnostiquer les includes, macros et force-includes ; il ne doit pas servir à recréer des AUs par profil.

## 2. Exécution CAST

Pour une arborescence avec Makefile classique et SDK Linux, sans `build.make` ni
`flags.make`, suivre [la variante Makefile / SDK](CAST_TEAM_PROCEDURE_MAKEFILE_SDK.md).
Elle produit un ZIP exploratoire autonome via `makefile_sdk_recover.py` et un profil
JSON relu. Les commandes ci-dessous concernent le flux de bundle Conan/QCC existant.

La commande reste inchangée :

```sh
python3 cast_offline_collector.py \
  --input-root /reception/CAST_DELIVERABLES_BUNDLE \
  --output-parent /work/cast-packages \
  --mode strict
```

Pour une baseline :

```sh
python3 cast_offline_collector.py \
  --input-root /reception/CAST_DELIVERABLES_BUNDLE \
  --output-parent /work/cast-packages \
  --baseline /work/cast-packages/previous-package \
  --mode strict
```

## 3. Diagnostic

Si le package est `NOT_QUALIFIED`, renvoyer le diagnostic à l’équipe client en précisant si le problème semble lié :

- aux makefiles modifiés ;
- au staging incomplet ;
- à une compilation database non conforme ;
- à des chemins Conan ou SDK non remappables ;
- à des probes compilateur manquants ;
- à une divergence entre le build local produit et les fichiers transférés.

Ne pas corriger le bundle à la main. La correction doit être faite côté client, puis rejouée par un nouveau build local.
