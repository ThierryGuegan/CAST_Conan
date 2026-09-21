# Politique de sécurité

Ce dépôt traite des sources propriétaires, des chemins de cache Conan, des journaux de build et des en-têtes de SDK. Ne jamais joindre un bundle réel à un ticket public.

## Signalement

Signaler les vulnérabilités par le canal sécurité interne de l’organisation, avec la version du collecteur, le scénario de reproduction minimal et l’impact estimé. Ne pas inclure de secret ni de source client dans le signalement.

## Principes appliqués

- aucune connexion réseau et aucun lancement de Conan/Make/compilateur par le collecteur CAST ;
- aucune commande shell construite à partir du bundle ;
- rejet des liens symboliques dans le bundle reçu ;
- limites de volumétrie configurables ;
- contrôle exhaustif du manifeste client ;
- détection de secrets et expurgation côté export client ;
- archive absente en cas d’échec strict.

`FILES.sha256` protège l’intégrité, pas l’authenticité. Une signature d’entreprise doit être vérifiée avant l’exécution du collecteur lorsque le transfert traverse une zone non fiable.
