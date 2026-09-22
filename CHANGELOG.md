# Journal des versions

## 2.1.0

- dépôt Git de production, quality gate et pipelines GitHub/GitLab sans dépendance d’exécution externe ;
- suppression des faux écarts de baseline causés par les chemins temporaires et horodatages ;
- matérialisation contrôlée des liens symboliques internes aux arbres Conan/QNX ;
- tests complémentaires de sécurité, baseline et export client ;
- procédures séparées pour les équipes client et CAST, avec le mode opératoire de production intégré à la procédure CAST ;
- variante locale dédiée aux clients fournissant des makefiles modifiés ;
- audit dédié des archives de build brutes reçues hors staging complet ;
- récupération partielle depuis les traces CMake/Conan lorsque le staging complet ne peut pas être régénéré.

## 2.0.0

- mise en œuvre initiale de la collecte stricte et des contrôles de qualification ;
- collecte stricte hors ligne, profils de compilation, remapping et qualification.
