# Fiche de contexte client — variante makefiles modifiés

Cette fiche accompagne un bundle produit depuis une version du code dont les makefiles ont été adaptés pour CAST.

## 1. Identification

| Champ | Valeur |
|---|---|
| Application |  |
| Version ou release |  |
| Commit applicatif |  |
| Commit ou tag contenant les makefiles modifiés |  |
| Identifiant local de génération (`--run-id`) |  |
| Contact capable de corriger les makefiles |  |

## 2. Staging CAST

| Champ | Valeur |
|---|---|
| Répertoire de staging produit par les makefiles |  |
| Cible make utilisée |  |
| Le staging est produit pendant le build local qualifié | Oui / Non |
| Le staging est archivé sans modification manuelle | Oui / Non |
| Méthode de génération de `compile_commands.json` |  |
| Méthode de copie des headers Conan |  |
| Méthode de copie des headers SDK/toolchain |  |

## 3. Périmètre

| Champ | Valeur |
|---|---|
| Cible |  |
| Architecture |  |
| OS cible |  |
| Type de build |  |
| Profil Conan host |  |
| Profil Conan build |  |
| Variante compilateur |  |

## 4. Points de vigilance

Indiquer ici les différences introduites par les makefiles modifiés :

- nouvelle cible make ;
- fichiers copiés en plus ou en moins ;
- chemins réécrits ;
- règles spécifiques CAST ;
- limites connues ;
- éléments non couverts par les makefiles.
