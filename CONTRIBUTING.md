# Contribution

1. Créer une branche depuis la version validée.
2. Modifier le code sans ajouter d’appel réseau ou d’exécution de build côté collecteur CAST.
3. Ajouter un test pour chaque correction ou nouveau format accepté.
4. Exécuter `python3 ci/quality_gate.py`.
5. Mettre à jour `CHANGELOG.md` et, si nécessaire, les schémas et exemples.
6. Faire relire toute modification touchant le remapping, les secrets, les archives ou l’inventaire Conan.

Une modification n’est livrable que si le quality gate est vert sur Python 3.9 et sur la version Python de production.
