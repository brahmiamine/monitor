# Inventaire automatique — suivi des cycles

Ce document décrit les capacités techniques et les lacunes. Il ne contient ni
adresses du serveur, noms de domaines métier, secrets, données de bases ni journaux.

## Audit initial, 27 octobre 2026

Le serveur examiné est un Linux Debian 13 sous KVM, avec systemd, cgroups v2,
interfaces PSI du noyau, Docker/Compose et un moteur MariaDB en fonctionnement.
Les logiciels SMART/NVMe, LVM, ZFS/Btrfs et les clients des pare-feu ne sont pas
disponibles dans le PATH du compte d'audit. Cela ne prouve ni l'absence du matériel
ni celle de règles de filtrage. AppArmor indique être activé. La synchronisation
NTP est active. Le disque principal est un disque virtuel avec ext4 et partition EFI.

L'audit est effectué sans sudo, sans ouverture de base métier et sans lecture des
fichiers d'identifiants. Le service de production utilise un compte système dédié,
plus restreint que le compte d'audit. Les capacités affichées par l'application
sont donc celles de **son propre contexte d'exécution**.

Le dépôt était propre à `202fab2`. Le service existant était actif et sans
redémarrage depuis sa dernière mise à jour. La référence de consommation observée
était d'environ 35 Mo dans son cgroup ; cette mesure est ponctuelle. Les contrôles
historiques, projets, sessions et métriques sont conservés.

## Matrice de couverture avant les nouveaux lots

| Groupe | Disponible localement | Collecté | Stocké avant | API avant | Interface avant | Lacune / limite |
|---|---|---|---|---|---|---|
| OS, noyau, architecture, KVM, uptime, NTP | proc/sys/systemd | Oui | Cache mémoire | live/stream | Système | Pas de fraîcheur par collecteur |
| CPU, RAM, swap, charge | proc/psutil | Oui | samples toutes les 10 s | live/history | Serveur et graphiques | PSI et limites effectives absents |
| Disques, montages, inodes, E/S | proc/sys/psutil | Oui | samples agrégés | live | Système | Topologie avancée et santé non disponibles |
| Interfaces, adresses, compteurs, ports | proc/psutil | Oui | Débits agrégés | live | Serveur/Système | Aucun historique des changements de ports ; bind != exposition Internet |
| Services actifs/arrêtés/échoués | systemd | Liste simple | Cache | live | Services | Unités installées non chargées, activation, dépendances et ressources manquantes |
| Timers | systemd JSON | Non | Non | Non | Non | Collecte disponible sans privilège supplémentaire |
| Paquets et runtimes | dpkg, commandes version | Oui | Cache | software | Logiciels | Ancien code Debian seulement, statut par source insuffisant |
| Conteneurs, CPU/RAM/IO/réseau | Docker via proxy lecture seule | Oui | Cache | live/discover | Conteneurs | Métadonnées partiellement affichées, pas d'historique |
| Images et volumes Docker | Docker | Oui | Cache | software | Logiciels | Relations conteneur/réseau/volume manquantes |
| Domaines, TLS, applications | Projets et contrôles configurés | Partiel | projects/checks | projects/monitors | Projets | Pas de découverte sûre de tous les reverse proxies |
| Moteurs de bases | Processus et sockets locaux | Version binaire parfois | Non | software | Logiciels | Absence de détection structurée ; métriques internes nécessitent accès dédié |
| États de sécurité | sysfs + outils disponibles | NTP/MAJ/échecs unités | Incidents | live | Système | AppArmor/SELinux/permissions non explicités |
| Sessions utilisateur | psutil | Identités et origines | Cache | live | Système | Collecte excessive : remplacée par un compte agrégé |
| Journaux, SSH, cron, firewall, certificats locaux | Variable selon permissions | Non | Non | Non | Non | Lecture de texte libre à éviter ; consentement/filtrage et interface à définir |
| Versions et déploiements | Métadonnées configurées | Partiel | projects | projects | Projets | Ne pas parcourir de répertoires privés implicitement |

## Dix améliorations prioritaires

| Rang | Amélioration | Valeur et choix de sûreté |
|---|---|---|
| 1 | Registre de collecteurs, état/fraîcheur/raison d'indisponibilité | Réutiliser l'existant ; erreur isolée et ressources bornées |
| 2 | PSI CPU/mémoire/IO et limites cgroups | Explique la contention sans inventer de capacité matérielle |
| 3 | Inventaires datés, changements et exports | Schéma additif séparé ; historique borné |
| 4 | Unités systemd installées, activation, redémarrages, ressources/dépendances | Propriétés autorisées uniquement ; pas de commandes de service |
| 5 | Timers et prochaine/dernière exécution | Lecture systemd, pas de contenu des tâches |
| 6 | Détection des moteurs de bases avec preuves | Noms de processus et sockets ; aucun identifiant ni requête métier |
| 7 | Topologie Docker : réseaux et volumes associés | Inspect sélectif ; aucune variable d'environnement ni label arbitraire |
| 8 | Page Inventaire filtrable et historique de chaque groupe | Compléter les données déjà collectées, distinguer zéro et inconnu |
| 9 | Adaptateurs logiciels RPM et sorties partielles | Portabilité sans installation automatique d'outils |
| 10 | Reverse proxies/domaines/TLS avec provenance | Prévoir chemins autorisés et métadonnées filtrées, ne pas inférer les dépendances DB |

## Cycle 1 — collecteurs et historique

Implémentation additive dans FastAPI et l'interface existante, sans changement de
framework ni nouvelle dépendance. Deux travailleurs maximum pour l'inventaire,
fréquences indépendantes et délais maximaux ; les prélèvements hôte toutes les
deux secondes restent séparés. Une tâche Python expirée garde son emplacement
jusqu'à sa fin : aucun empilement illimité de threads. Les nouveaux sous-processus
ont une durée, une sortie et un environnement bornés ; aucun shell ni stderr brut.

Les collecteurs de matériels, logiciels, conteneurs et services déjà présents
alimentent leurs anciennes API et le nouveau registre. Les nouvelles API privées
servent l'inventaire, les snapshots, différences, métriques et exports. Les requêtes
de lecture consultent des caches ou l'historique et ne lancent pas de commandes.

Les tables `inventory_*` séparent états, snapshots, mesures, changements,
relations et configuration. La migration est idempotente, et son retour arrière
explicite ne touche pas les tables historiques. Les données sont limitées à
256 Kio par résultat persisté, 48 snapshots par collecteur, 5 000 mesures et
1 000 changements par collecteur, avec une rétention maximale de 30 jours.
Ces plafonds sont indépendants : la limite d'enregistrements peut réduire la
durée visible. Les mesures PSI ne génèrent pas d'événement de changement à
chaque fluctuation. Les capacités partielles et les sources inaccessibles sont
indiquées dans les résultats.

Les sessions utilisateur sont agrégées. Les commandes Docker ne demandent plus
les labels arbitraires, et les URL OCI de dépôts contenant des identifiants ou
paramètres sont exclues. Le premier démarrage ne crée un contrôle SSH que si un
service et un socket correspondants sont observés.

## Validation et déploiement

Validation locale finale : 91 tests Python réussis (dont migration, retour arrière
et transfert d'historique vers un vrai MariaDB isolé), 17 tests JavaScript et un
parcours Chromium ordinateur/mobile réussi avec comparaison d'inventaires et
export. Une revue indépendante n'a pas relevé de défaut matériel restant.
Les migrations sont exécutées sur SQLite temporaire et sur une instance MariaDB
locale séparée, créée sans root, sur socket privé sans port réseau. Le site de
test utilise un dossier de données distinct et ne lit pas la configuration de
production. Une sauvegarde Git de référence est conservée hors dépôt.

## Cycle 2 — topologie et portabilité des logiciels

Le collecteur Docker décrit uniquement les liens observés entre un conteneur et
ses réseaux ou volumes nommés. La collecte locale relève 15 conteneurs et 18
relations au moment du contrôle ; ces nombres décrivent cet audit, pas une
configuration imposée. Les chemins des bind mounts et les paramètres sensibles
ne sont pas lus. L'adaptateur dpkg relève 642 paquets sur la machine contrôlée ;
l'adaptateur RPM est vérifié sur fixtures car RPM n'est pas installé ici.

Les versions de runtimes utilisent des commandes fixes avec sortie bornée, y
compris stderr uniquement pour ces requêtes de version. Les autres stderr ne sont
pas conservés. Une inspection Docker inaccessible donne des valeurs inconnues,
jamais zéro redémarrage ou absence d'OOM inventés. Le transfert de base a été
placé avant le démarrage des travailleurs pour préserver les identifiants des
snapshots et les références des changements.

## Mesures intégrées du processus de test

| Mesure | Observation |
|---|---|
| Collecte ordinaire, sans requêtes API pendant 30 s | 1,39 s CPU du processus, soit 4,63 % d'un cœur ; 0,24 s CPU d'enfants récoltés en plus |
| Mémoire maximale observée sans requêtes rapprochées | 56,56 Mio RSS |
| 80 requêtes privées d'inventaire, environ 3/s | Médiane 35,25 ms, p95 54,85 ms, 56,91 Mio RSS |
| CPU durant cette charge de lecture | 15,15 % d'un cœur pour le processus, CPU des sous-processus exclu |
| Concurrence observée | 16 threads du processus, dont au plus 2 collecteurs et 1 travailleur d'historisation |
| Taille de la base temporaire après les essais successifs | Environ 2,6 Mio ; contient aussi les données des précédentes validations |
| API et persistance à la fin du contrôle | 12 groupes disponibles, aucune écriture en attente ou erreur remontée |

Ces fenêtres sont courtes et dépendent de la charge du VPS. Elles ne prouvent ni
une moyenne sur une heure, ni l'absence de fuite mémoire à long terme. La référence
de 35 Mo du service déjà installé est une mesure cgroup, tandis que les 56–57 Mio
ci-dessus sont le RSS d'un processus de test distinct : ne pas les soustraire
comme un surcoût exact. La sérialisation de l'API a été allégée après une première
mesure plus lente. Le service de production a conservé son PID et zéro redémarrage
pendant ces essais. Aucun accès à sa base ni modification de permissions.

Les limites de consommation, les tests d'outils absents, les délais et les files
bloquées sont vérifiés par les tests automatisés ; les observations réelles ne
remplacent pas ces cas dégradés. Un avertissement de dépréciation Starlette/httpx
de la suite existante reste présent, sans échec de test ni ajout de dépendance.

**Point de décision opérationnel :** le push sur `main` déclenche ici un
déploiement automatique, redémarre Monitor et applique le schéma. L'intégration
est préparée sur une branche isolée. Avant publication, l'opérateur doit valider
la migration et le redémarrage, et disposer d'une sauvegarde cohérente de la base
de production. Aucun fichier de credentials n'est ouvert pour réaliser cette
sauvegarde sans autorisation.

## Lacunes conservées explicitement

- La présence d'un port habituel de base ne prouve pas un moteur ; les preuves
  de processus et les candidats de ports restent distincts.
- Pas d'accès aux métriques internes des moteurs sans connexion dédiée autorisée.
- Pas de SMART/NVMe, RAID/ZFS/LVM, audit de journaux ou règles firewall prétendu
  complet si les interfaces ou permissions nécessaires ne sont pas disponibles.
- Pas de recherche exhaustive des applications, secrets, bases SQLite ou fichiers
  de certificats sur le disque ; chemins explicitement autorisés à définir.
- Pas de notification de nouveau port automatiquement activée avant une politique
  de bruit et d'exposition validée ; les incidents existants restent fonctionnels.
- Les relations par réseau partagé indiquent une connectivité, pas une dépendance
  applicative ni un accès avéré à une base.
- Les métriques par interface, par service et par volume nécessitent encore des
  séries dédiées, des unités et des agrégations adaptées.

## Intégration avec les évolutions de main

La livraison conserve également les notifications persistantes, les diagnostics
SLA/base, les relations explicites projets/contrôles, les utilitaires de sauvegarde
et la PWA ajoutés sur main pendant ce travail. Les alertes Docker sont exécutées
par le travailleur de persistance, sans bloquer les collecteurs. Une inspection
inconnue ne clôture pas une alerte existante. La santé HTTP distingue un outil
facultatif absent dès le démarrage d'une capacité attendue réellement en échec.

Validation de l'ensemble fusionné : 98 tests Python, 17 tests JavaScript et un
parcours Chromium ordinateur/mobile réussis ; une revue indépendante de la fusion
ne relève pas de défaut matériel restant. La publication et le déploiement ont
été autorisés explicitement par le propriétaire après préparation de la sauvegarde.
