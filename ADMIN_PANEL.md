# Admin Panel - Gestion simplifiée des mises à jour

Vous pouvez maintenant gérer les mises à jour du CGP Monitor **directement depuis le site** via le nouveau panneau Admin.

## Accès au panneau Admin

1. Allez sur https://lsebah.github.io/cgp-monitor/
2. Cliquez sur le bouton **"Admin"** dans l'en-tête (à côté de Sync, Notif, Export)
3. Le panneau Admin s'ouvre avec 4 sections principales

---

## 📊 Section 1: État des mises à jour

Affiche le statut du dernier scrape:
- **Date et heure** du dernier scrape
- **Statut** (✅ Succès, ⏳ En cours, ❌ Échec)
- **Lien direct** vers GitHub Actions pour plus de détails

Vous pouvez aussi **déclencher un scrape immédiatement** en cliquant "Lancer le scrape maintenant"

---

## 🔍 Section 2: Ajouter des CGPs manquantes

C'est ici que vous gérez les CGPs qu'il faut chercher automatiquement.

### Comment ajouter une CGP manquante

1. Tapez le nom dans le champ (ex: "Alpy Gestion")
2. Vous pouvez ajouter plusieurs à la fois en les séparant par des virgules:
   ```
   Alpy Gestion, Blackwell Family Office, Autre Cabinet
   ```
3. Cliquez "Ajouter"
4. Les noms sont sauvegardés localement sur votre appareil

### Comment supprimer une CGP

Cliquez sur le ×/bouton de suppression à côté du nom dans la liste "CGPs actuellement recherchées"

### Comment ça marche

À chaque scrape (quotidien ou manuel), le scraper:
1. Lance ORIAS pour chaque nom de la liste
2. Récupère le numéro ORIAS
3. Vérifie le statut (actif/inactif)
4. Ajoute la CGP à la base de données

**C'est automatique!** Vous ajoutez le nom une fois, le scraper s'en charge.

---

## 📈 Section 3: Statistiques

Affiche les chiffres clés:
- **Total CGPs** - Cabinets détectés dans la base
- **Nouveaux cette semaine** - CGPs détectées cette semaine
- **Nouveaux ce mois** - CGPs détectées ce mois-ci
- **Dernière mise à jour** - Date du dernier scrape

---

## 🔐 Section 4: Configuration GitHub (Optionnel)

Pour **déclencher des scrapes directement depuis le site**, vous avez besoin d'un token GitHub.

### Pourquoi?

Sans token: impossible de déclencher un scrape depuis le site
Avec token: cliquez "Lancer le scrape maintenant" et c'est fait!

### Comment configurer

1. Créez un token GitHub: https://github.com/settings/tokens
   - Scope requis: **actions:write**
   - Les autres scopes sont optionnels
   - Nommez-le "CGP Monitor Admin"

2. Copiez le token (starts with `ghp_`)

3. Dans le panneau Admin:
   - Collez le token dans "Token GitHub Actions"
   - Vérifiez que "Dépôt" est correct (`lsebah/cgp-monitor`)
   - Cliquez "Enregistrer"

### ⚠️ Sécurité

- ✅ Le token est **sauvegardé localement** sur votre appareil (localStorage)
- ✅ Il n'est **jamais envoyé** ailleurs que vers api.github.com
- ✅ Le token ne vous permet que de **déclencher les workflows** (actions:write)
- ⚠️ Ne partagez **jamais votre token** avec d'autres

---

## ⚙️ Flux de travail complet

### Option 1: Automatique (Recommandé)

1. GitHub Actions scrape **chaque jour à 02:00 UTC**
2. Cherche TOUTES les CGPs (CNCGP, CNCEF, ANACOFI, AFFO, ORIAS)
3. Ajoute les manquantes de votre liste
4. Met à jour le site automatiquement
5. ✨ Vous n'avez rien à faire!

### Option 2: Manuel (Quand vous le voulez)

1. Allez sur Admin Panel
2. Cliquez "Lancer le scrape maintenant"
3. Attendez ~5 minutes
4. Le site se met à jour automatiquement

### Option 3: Hybride

- Les mises à jour automatiques quotidiennes se font à 02:00
- Vous pouvez déclencher un scrape supplémentaire quand vous découvrez une CGP manquante
- Idéal pour avoir les données ultra-fraîches

---

## 💡 Cas d'usage pratiques

### Exemple 1: Vous découvrez une CGP manquante

1. Vous trouvez "Alpy Gestion" qui n'est pas sur le site
2. Vous ouvrez Admin Panel
3. Vous tapez "Alpy Gestion" dans le champ
4. Vous cliquez "Ajouter"
5. Optionnel: vous cliquez "Lancer le scrape maintenant"
6. ✅ La CGP est ajoutée à la liste de recherche
7. Le prochain scrape (auto ou manuel) la trouvera sur ORIAS

### Exemple 2: Vous avez une liste de CGPs à ajouter

1. Vous collez toute la liste séparée par des virgules:
   ```
   Cabinet A, Cabinet B, Cabinet C, Cabinet D
   ```
2. Cliquez "Ajouter"
3. ✅ Les 4 cabinets sont ajoutés
4. Le scraper les cherchera tous

### Exemple 3: Vous voulez une mise à jour immédiate

1. Vous venez d'ajouter des CGPs
2. Vous cliquez "Lancer le scrape maintenant"
3. GitHub Actions s'exécute immédiatement
4. ~5 minutes après, le site se met à jour

---

## 📱 Devices

Les données Admin sont **stockées localement** sur chaque appareil:

- **PC**: ton token + ta liste de CGPs sur le PC
- **iPhone**: sa liste peut être différente
- **Tablette**: ses propres paramètres

Pour **synchroniser entre devices**, utilise la **synchronisation GitHub Gist** (bouton Sync) qui synchronise les statuts et Folk, mais pas la config Admin (c'est volontaire pour la sécurité).

---

## 🆘 Dépannage

### Le bouton Admin n'apparaît pas?

- Vérifiez que vous avez la dernière version (Ctrl+Shift+R ou Cmd+Shift+R pour forcer)
- Vérifiez la console (F12) pour les erreurs JavaScript

### Le scrape ne se déclenche pas?

- Vérifiez que vous avez un token GitHub valide
- Vérifiez que le token a le scope `actions:write`
- Vérifiez le dépôt: `lsebah/cgp-monitor`
- Allez sur https://github.com/lsebah/cgp-monitor/actions pour voir les logs

### Les CGPs ajoutées n'apparaissent pas?

- Attendez le prochain scrape (automatique à 02:00 ou manuel)
- Vérifiez que les noms sont corrects (orthographe, accents)
- Allez vérifier sur ORIAS: https://www.orias.fr/search
- Si la CGP n'existe pas sur ORIAS, elle ne peut pas être trouvée

### Mon token ne fonctionne pas?

- Vérifiez que le token commence par `ghp_`
- Vérifiez que le scope inclut `actions:write`
- Testez le token sur https://github.com/settings/tokens
- Créez un nouveau token si celui-ci est révoqué

---

## 🎯 Résumé

| Tâche | Avant | Après |
|-------|-------|-------|
| Ajouter une CGP manquante | Aller sur GitHub, éditer le code | Cliquer sur Admin, taper le nom ✅ |
| Déclencher un scrape | Aller sur GitHub Actions | Cliquer "Lancer le scrape" ✅ |
| Voir l'état du scrape | Aller sur GitHub Actions | Voir dans Admin Panel ✅ |
| Ajouter plusieurs CGPs | Une par une | Séparer par des virgules ✅ |
| Facilité d'utilisation | Pour développeurs | Pour tout le monde ✅ |

---

## 🚀 C'est tout!

Vous pouvez maintenant gérer CGP Monitor directement depuis le site, sans jamais toucher au GitHub. Facile et intuitif! 🎉
