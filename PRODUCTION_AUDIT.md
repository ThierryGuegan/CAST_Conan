# Audit final de production — version 2.1.0

## Verdict

**GO pour pilote contrôlé sur les applications A et B.** La mise en référence définitive reste conditionnée à la réussite d’un build réel par cible, à une couverture de 100 % et à la qualification des premiers logs CAST.

## Périmètre contrôlé

- collecteur exécuté par l’équipe CAST sans Conan, Make, GCC/QCC ni réseau ;
- exporteur post-build exécuté exclusivement dans la CI cliente ;
- inventaire Conan 2 exact ;
- preuves GCC/QCC, chemins d’include, macros, sysroot, response files et `.d` ;
- intégrité, secrets, liens symboliques, volumétrie et erreurs opérationnelles ;
- reproductibilité du package et intégration Git/CI.

## Défauts corrigés pendant la mise en production

| Sévérité initiale | Défaut | Correction | État |
|---|---|---|---|
| Haute | baseline toujours différente à cause des chemins/horodatages | comparaison canonique et sémantique | Clos, test automatisé |
| Haute | secret potentiellement transféré dans un build log | expurgation en flux côté client puis scan bloquant côté CAST | Clos, test automatisé |
| Haute | liens internes QNX/Conan incompatibles avec un bundle sans symlink | matérialisation contrôlée, rejet des sorties de racine et cycles | Clos, test automatisé |
| Moyenne | response-file ou probe excessif pouvant saturer la mémoire | limites bloquantes dédiées | Clos |
| Moyenne | lecture complète des grands logs | scan, expurgation et qualification en flux | Clos |
| Moyenne | erreur système non normalisée | statut `OPERATIONAL_ERROR`, code retour 3 | Clos |
| Moyenne | manifeste client vérifié mais non conservé | copie dans les preuves du package | Clos |
| Moyenne | distribution non reproductible | générateur ZIP déterministe et checksum | Clos, comparaison binaire réussie |
| Faible | absence de dépôt/CI/runbook | dépôt Git, pipelines, quality gate, runbook et checklist | Clos |

## Vérifications réalisées

- compilation syntaxique de tous les modules ;
- validation JSON des schémas et exemples ;
- contrôle AST interdisant `shell=True` ;
- 17 tests unitaires et d’intégration réussis ;
- parcours client → bundle → collecteur → `READY_FOR_ANALYSIS` réussi ;
- baseline identique sans faux positif ;
- installation du projet et import des trois points d’entrée réussis ;
- archive reproductible bit à bit et test ZIP sans erreur ;
- dépôt Git sans modification non commitée au moment de la livraison.

## Risques résiduels acceptés pour le pilote

1. SHA-256 ne prouve pas l’identité de l’émetteur : signature d’entreprise requise pour une chaîne de confiance complète.
2. Un graphe et un `compile_commands.json` cohérents peuvent néanmoins être mensongers si la CI cliente est compromise : utiliser une attestation de pipeline si disponible.
3. Le scanner de secrets est une seconde barrière, pas un DLP exhaustif.
4. Les particularités QCC non présentes dans les commandes ou probes doivent être découvertes au pilote et ajoutées aux tests.
5. La compatibilité Python 3.9 est couverte par la CI déclarative ; l’exécution locale de cet audit a été réalisée avec Python 3.12.

## Critères de sortie du pilote

- App A et App B traitées séparément ;
- un bundle par cible/configuration ;
- statut `READY_FOR_ANALYSIS` sans critique ;
- couverture C/C++ à 100 % ;
- vérification manuelle d’un échantillon de profils face au journal réel ;
- absence d’erreur de header, préprocesseur ou parseur dans CAST ;
- dérive de baseline expliquée et approuvée ;
- signature et archivage interne activés.
