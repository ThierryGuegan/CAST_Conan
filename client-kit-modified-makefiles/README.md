# Kit client — variante avec makefiles modifiés

Ce kit s’applique lorsque l’équipe client a modifié ses makefiles pour produire un staging CAST pendant un build local qualifié.

Il ne remplace pas `client-kit/`. Il couvre uniquement le scénario où les makefiles préparent déjà les livrables dans un dossier de staging.

## Contenu à transmettre

```text
docs/CLIENT_TEAM_PROCEDURE_MODIFIED_MAKEFILES.md
docs/CAST_TEAM_PROCEDURE_MODIFIED_MAKEFILES.md
client-kit-modified-makefiles/README.md
client-kit-modified-makefiles/CLIENT_CONTEXT_FORM_MODIFIED_MAKEFILES.md
client-kit-modified-makefiles/LOCAL_EXPORT_COMMANDS.md
makefile_local_export.py
makefile_local_audit.py
makefile_local_recover.py
cast_offline_collector.py
schemas/
examples/
```

## Flux attendu

1. Le build client s’exécute localement avec les makefiles modifiés.
2. Les makefiles copient les livrables CAST dans un staging local, par exemple `$PWD/build/cast/staging`.
3. `makefile_local_export.py` normalise ce staging en `CAST_DELIVERABLES_BUNDLE`.
4. L’équipe client transmet le bundle et la fiche de contexte à CAST.
5. L’équipe CAST exécute le collecteur hors ligne.

Une archive contenant seulement `build/` sert au diagnostic, pas au transfert CAST. Elle doit être auditée avec `makefile_local_audit.py` et complétée côté client avant export.

## Commande type

```sh
python3 makefile_local_export.py \
  --staged-root "$PWD/build/cast/staging" \
  --bundle "$PWD/build/cast/CAST_DELIVERABLES_BUNDLE" \
  --application "$APPLICATION" \
  --git-commit "$GIT_COMMIT" \
  --run-id "$LOCAL_RUN_ID" \
  --target-label "$TARGET_LABEL" \
  --target-os "$TARGET_OS" \
  --architecture "$TARGET_ARCH" \
  --build-type "$BUILD_TYPE" \
  --compiler "$COMPILER" \
  --compiler-variant "$COMPILER_VARIANT" \
  --host-profile "$HOST_PROFILE" \
  --build-profile "$BUILD_PROFILE" \
  --source-root-at-build "$SRC_ROOT"
```

Si le staging fournit déjà `identity/BUILD_IDENTITY.json`, les options d’identité servent seulement de secours. Utiliser `--force-identity` pour régénérer l’identité depuis les arguments locaux.

## Audit d’une archive reçue incomplète

Si l’équipe CAST reçoit une arborescence locale incomplète, contrôler son contenu avant toute tentative de collecte :

```sh
python3 makefile_local_audit.py --root /local/LCCS_Archive_CAST
```

Le statut `DIAGNOSTIC_ONLY` signifie que des traces de build sont présentes, mais que l’archive ne contient pas tous les livrables attendus. Le client doit régénérer le staging complet avec les makefiles modifiés.

Si cette régénération n’est pas possible, l’équipe CAST peut créer un dossier de récupération partielle à partir de la liste d’arborescence :

```sh
python3 makefile_local_recover.py \
  --root /local/LCCS_Archive_CAST \
  --output recovered-from-root
```

Ce dossier contient un `recovery-report.json` qui liste les applications détectées et les éléments encore absents pour une collecte stricte. Ajouter `--application <NOM_APPLICATION>` uniquement pour isoler un module.

## Contrôle avant transfert

Le script retourne `READY_FOR_TRANSFER` si les entrées minimales sont présentes. Un statut `NOT_READY_FOR_TRANSFER` doit être corrigé côté client avant envoi à CAST.
