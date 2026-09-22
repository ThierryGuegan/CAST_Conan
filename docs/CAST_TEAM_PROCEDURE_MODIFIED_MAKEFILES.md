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
python3 makefile_local_audit.py --root /reception/LCCS_Archive_CAST
```

Un statut `DIAGNOSTIC_ONLY` signifie que l’arborescence contient des traces utiles pour expliquer le build, mais qu’elle ne contient pas tous les livrables nécessaires. Si le client ne peut pas régénérer un staging complet, utiliser un flux de récupération partielle basé sur le contenu réel du répertoire racine :

```sh
python3 makefile_local_recover.py \
  --root /reception/LCCS_Archive_CAST \
  --output /work/recovered-from-root
```

Ce flux exploite les répertoires `build` présents sous la racine pour toutes les applications détectées. Pour limiter le traitement à un module, ajouter `--application <NOM_APPLICATION>`. Il est utile pour reconstruire un `compile_commands.json` partiel, rattacher les en-têtes Conan host identifiés par les métadonnées Conan disponibles et récupérer les probes compilateur présentes sous `compiler/`.

La configuration CAST Imaging ne doit pas être créée à la main à partir de l’arborescence brute. Elle doit s’appuyer sur le package produit par `cast_offline_collector.py` lorsque le bundle est qualifié : `cast-config/analysis-units.json`, `cast-config/compilation-profiles.json`, `cast-config/force-include-*.h`, `cast-config/dependency-files.csv` et `CAST_ANALYSIS_PLAN.md`.

## 2. Exécution CAST

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
