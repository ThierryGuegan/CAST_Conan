# Checklist de release

- [ ] `COLLECTOR_VERSION` et `pyproject.toml` portent la même version.
- [ ] `python3 ci/quality_gate.py` réussit.
- [ ] Les tests pilote A et B réussissent pour chaque cible retenue.
- [ ] Les journaux CAST ne contiennent aucun header manquant ni erreur de préprocesseur.
- [ ] La dérive par rapport à la baseline est expliquée.
- [ ] L’archive est produite par `python3 tools/release.py`.
- [ ] Le fichier `.sha256` est conservé avec l’archive.
- [ ] L’archive est signée selon la procédure de l’entreprise.
- [ ] Le tag Git signé correspond à la version livrée.
- [ ] Le rollback vers la version précédente a été vérifié.
