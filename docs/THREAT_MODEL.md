# Modèle de menace

| Risque | Contrôle | Risque résiduel |
|---|---|---|
| Traversée de chemin | résolution sous racine et rejet | différences de sémantique de chemins entre OS |
| Lien symbolique malveillant | rejet côté CAST ; matérialisation interne contrôlée côté client | SDK comportant des liens externes légitimes à traiter explicitement |
| Secret dans les preuves | expurgation des logs côté client puis scan bloquant côté CAST | formats de secrets non reconnus |
| Substitution de package Conan | identité exacte RREV/PREV/package ID et contrôle du graphe | graphe mensonger sans attestation CI |
| Bundle modifié | manifeste exhaustif SHA-256 | manifeste lui-même remplaçable sans signature |
| Déni de service | limites fichiers/octets et profondeur response-file | fichier texte pathologique sous les limites |
| Exécution de code | aucune importation/exécution du contenu reçu | vulnérabilité de l’interpréteur/OS |
| Fausse fidélité de compilation | profils par commande, probes QCC, couverture 100 % | option propriétaire non modélisée |

Le bundle doit être considéré comme non fiable jusqu’à la fin des contrôles. Le package produit reste confidentiel car il contient du code et des en-têtes propriétaires.
