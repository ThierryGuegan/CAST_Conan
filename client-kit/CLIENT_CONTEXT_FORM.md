# Fiche de contexte client

Cette fiche accompagne le premier transfert d’un bundle de livrables CAST. Elle ne remplace pas le bundle produit par la CI ; elle donne à l’équipe CAST le contexte nécessaire pour qualifier et exploiter le package sans multiplier les allers-retours.

Renseigner uniquement les champs applicables au périmètre transmis.

## 1. Identification

| Champ | Valeur |
|---|---|
| Application |  |
| Version applicative ou release |  |
| Commit |  |
| Pipeline / job CI |  |
| Date du build |  |
| Équipe propriétaire |  |
| Contact de régénération du bundle |  |
| Canal de retour en cas de rejet |  |

## 2. Périmètre analysé

| Champ | Valeur |
|---|---|
| Cible |  |
| Architecture |  |
| Système d’exploitation cible |  |
| Type de build |  |
| Langages attendus dans CAST |  |
| Sources explicitement hors périmètre |  |
| Bundles liés ou dépendants |  |

## 3. Build qualifié

| Champ | Valeur |
|---|---|
| Outil de build principal |  |
| Commande de build produit |  |
| Méthode de production de `compile_commands.json` |  |
| Emplacement de `compile_commands.json` dans le build |  |
| Confirmation que `compile_commands.json` provient du build qualifié | Oui / Non |
| Fichiers `.rsp` utilisés | Oui / Non |
| Fichiers `.d` disponibles | Oui / Non |
| Journaux inclus dans le bundle |  |

## 4. Conan et dépendances

| Champ | Valeur |
|---|---|
| Conan utilisé | Oui / Non |
| Version Conan |  |
| Profil host |  |
| Profil build |  |
| Lockfile utilisé | Oui / Non |
| Graphe Conan produit depuis le build qualifié | Oui / Non |
| Packages privés ou internes particuliers |  |
| Dépendances non-Conan importantes |  |

## 5. Toolchain et SDK

| Champ | Valeur |
|---|---|
| Compilateur |  |
| Version compilateur |  |
| Variante compilateur |  |
| SDK ou sysroot utilisé |  |
| Variables d’environnement importantes |  |
| Probes compilateur produits | Oui / Non |
| Langages couverts par les probes |  |
| Particularités connues de la toolchain |  |

## 6. Code généré

| Champ | Valeur |
|---|---|
| Code généré présent | Oui / Non |
| Générateur utilisé |  |
| Version du générateur |  |
| Identifiant de génération |  |
| Source modèle ou référentiel amont |  |
| Emplacement du code généré |  |

## 7. Contraintes de transfert

| Champ | Valeur |
|---|---|
| Nom de l’archive transmise |  |
| Canal de dépôt |  |
| Règle de nommage appliquée |  |
| Durée de rétention côté client |  |
| Délai typique pour régénérer un bundle |  |

## 8. Informations utiles à CAST

Ajouter ici toute information utile pour interpréter les logs ou le périmètre :

- exclusions connues ;
- warnings récurrents mais acceptés côté build ;
- sources générées ou copiées par un mécanisme spécifique ;
- particularités de chemins ;
- changement récent de toolchain, profil Conan ou SDK ;
- priorité d’analyse si plusieurs bundles sont transmis.
