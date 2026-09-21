# Runbook de production

## Préconditions

- bundle client produit après un build réussi ;
- transfert terminé et, si applicable, signature d’entreprise vérifiée ;
- Python 3.9+ ;
- espace disque libre supérieur à deux fois la taille du bundle plus celle du package estimé.

## Exécution

```sh
python3 cast_offline_collector.py \
  --input-root /reception/CAST_DELIVERABLES_BUNDLE \
  --output-parent /work/cast-packages \
  --mode strict \
  --baseline /work/cast-packages/previous
```

Le processus doit être lancé avec un compte sans privilège, dans un répertoire de travail dédié. Le répertoire de sortie ne doit pas se trouver sous le bundle d’entrée.

## Décision

| Code | Statut | Action |
|---:|---|---|
| 0 | `READY_FOR_ANALYSIS` | Revoir le plan, configurer CAST puis lancer l’analyse |
| 0 | `EXPLORATORY` | Diagnostic uniquement, jamais une baseline |
| 2 | `NOT_QUALIFIED` | Ne pas analyser ; corriger le bundle côté client |
| 3 | `OPERATIONAL_ERROR` | Corriger l’environnement, les permissions ou l’espace disque puis relancer |

Avant CAST, contrôler `collection-report.json`, `unresolved-paths.txt`, la couverture à 100 % et `baseline-diff.json`.

## Première mise en service

1. Exécuter un pilote sur une première application et une première cible.
2. Comparer les unités aux commandes de compilation d’un échantillon manuel.
3. Lancer CAST et fournir les logs dans `cast-analysis-logs/`.
4. Recollecter et obtenir une qualification sans erreur automatique.
5. Répéter sur chaque application et chaque cible réellement analysée.
6. Geler la version, le schéma et la baseline.

## Incident et rollback

- conserver le bundle d’entrée, le package et les logs ;
- ne jamais corriger un package produit à la main ;
- reproduire avec `--mode exploratory` dans un répertoire isolé ;
- revenir au tag précédent si une régression du collecteur est confirmée ;
- régénérer entièrement le package après correction.
