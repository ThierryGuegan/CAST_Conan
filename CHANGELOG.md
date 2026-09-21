# Journal des versions

## 2.1.0

- dépôt Git de production, quality gate et pipelines GitHub/GitLab sans dépendance d’exécution externe ;
- suppression des faux écarts de baseline causés par les chemins temporaires et horodatages ;
- expurgation des secrets des journaux avant leur transfert depuis la CI cliente ;
- matérialisation contrôlée des liens symboliques internes aux arbres Conan/QNX ;
- conservation du manifeste d’intégrité client dans les preuves ;
- tests complémentaires de sécurité, baseline et export client ;
- runbook, modèle de menace et checklist de release.

## 2.0.0

- mise en œuvre initiale des actions d’audit P0, P1 et P2 ;
- collecte stricte hors ligne, profils de compilation, remapping et qualification.
