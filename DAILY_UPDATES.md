# Mise à jour quotidienne automatique du CGP Monitor

Le CGP Monitor est configuré pour mettre à jour les données **tous les jours automatiquement**.

## Option 1: GitHub Actions (Recommandé)

Les mises à jour se font automatiquement via GitHub Actions à **02:00 UTC (03:00 CET)** chaque jour.

### Fonctionnement
- ✅ Le scraper s'exécute automatiquement tous les jours
- ✅ Recherche TOUTES les CGPs sur ORIAS (y compris les manquantes)
- ✅ Détecte automatiquement les nouvelles CGPs
- ✅ Commit et push les données mises à jour
- ✅ Les modifications sont visibles sur GitHub et sur le site

### Voir le statut
1. Allez sur: https://github.com/lsebah/cgp-monitor/actions
2. Cliquez sur "Daily CGP Scrape"
3. Voyez l'historique des scrapes et les résultats

### Exécution manuelle (si besoin)
1. Allez sur GitHub → Actions → Daily CGP Scrape
2. Cliquez sur "Run workflow"
3. Le scraper s'exécute immédiatement

---

## Option 2: Cron local (Serveur personnel)

Si vous hébergez vous-même, configurez le scraper via crontab:

```bash
./scraper/crontab-setup.sh
```

Cela ajoute une tâche cron pour exécuter le scraper à 02:00 chaque jour.

### Vérifier l'installation
```bash
crontab -l | grep cgp-monitor
```

### Logs
```bash
tail -f /tmp/cgp-monitor-scrape.log
```

---

## Détection automatique des CGPs manquantes

Le scraper recherche automatiquement sur ORIAS avec ces stratégies:

1. **"Conseil Investissements Financiers"** - Terme officiel
2. **"CIF"** - Abréviation
3. **"Gestion patrimoine"** - Service courant
4. **"Conseiller patrimoine"** - Intitulé de poste
5. **"Gestionnaire actifs"** - Gestion d'actifs
6. **"Conseils financiers"** - Terme général

Cela capture automatiquement:
- ✅ Alpy Gestion
- ✅ Blackwell Family Office
- ✅ **+ toutes les autres CGPs enregistrées sur ORIAS**

Aucune intervention manuelle requise! Les données sont à jour chaque jour.

---

## Ajouter plus de recherches

Pour améliorer la détection, éditez `scraper/sources/missing_cgps.py` et ajoutez des termes de recherche au tuple `search_queries`:

```python
search_queries = [
    "Conseil Investissements Financiers",
    "CIF",
    "Gestion patrimoine",
    # Ajoute ici: "Nouveau terme de recherche",
]
```

Le scraper incluera ces recherches à la prochaine exécution.

---

## Statistiques quotidiennes

À chaque scrape, les données suivantes sont collectées:

- **Total CGPs** - Nombre total de cabinets détectés
- **Nouveaux cette semaine** - Nouvelles CGPs détectées cette semaine
- **Nouveaux ce mois** - Nouvelles CGPs détectées ce mois
- **Par source** - Répartition par annuaire (CNCGP, CNCEF, ANACOFI, AFFO, ORIAS)
- **Par département** - Où sont situées les CGPs
- **Par association** - Quelles associations regroupent les CGPs

---

## Calendrier des mises à jour

| Heure | Action |
|-------|--------|
| 02:00 UTC | Scrape ORIAS, CNCGP, CNCEF, ANACOFI, AFFO |
| 02:15 UTC | Enrichissement des emails |
| 02:20 UTC | Détection des nouveaux cabinets |
| 02:25 UTC | Commit et push des données |
| 03:00 UTC | Site mis à jour |

*Tous les horaires en UTC. Ajouter +2 heures pour l'heure d'été française (CEST) ou +1 heure pour l'heure d'hiver (CET).*

---

## Dépannage

### Les données ne se mettent pas à jour?
1. Vérifiez https://github.com/lsebah/cgp-monitor/actions
2. Regardez les logs de la dernière exécution
3. Vérifiez les erreurs réseau ou de scraping

### ORIAS est indisponible?
Le scraper gère les erreurs gracieusement:
- Réessaye 3 fois avec délai exponentiel
- Continue si une source échoue
- Les autres sources restent à jour

### Besoin d'une mise à jour immédiate?
1. Allez sur GitHub Actions
2. Cliquez "Run workflow"
3. Les données se mettent à jour dans ~5 minutes

---

## Support

Pour plus d'informations:
- Consultez le code du scraper: `scraper/main.py`
- Configuration des sources: `scraper/config.py`
- Sources ORIAS: `scraper/sources/missing_cgps.py`
