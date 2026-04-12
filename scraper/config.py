"""
CGP Monitor - Configuration
French departments, source URLs, groupement reference data.
"""

# All French departments (metropolitan + overseas)
DEPARTMENTS = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardeche", "08": "Ardennes",
    "09": "Ariege", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhone", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Correze", "2A": "Corse-du-Sud",
    "2B": "Haute-Corse", "21": "Cote-d'Or", "22": "Cotes-d'Armor", "23": "Creuse",
    "24": "Dordogne", "25": "Doubs", "26": "Drome", "27": "Eure",
    "28": "Eure-et-Loir", "29": "Finistere", "30": "Gard", "31": "Haute-Garonne",
    "32": "Gers", "33": "Gironde", "34": "Herault", "35": "Ille-et-Vilaine",
    "36": "Indre", "37": "Indre-et-Loire", "38": "Isere", "39": "Jura",
    "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne",
    "48": "Lozere", "49": "Maine-et-Loire", "50": "Manche", "51": "Marne",
    "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse",
    "56": "Morbihan", "57": "Moselle", "58": "Nievre", "59": "Nord",
    "60": "Oise", "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dome",
    "64": "Pyrenees-Atlantiques", "65": "Hautes-Pyrenees", "66": "Pyrenees-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhone", "70": "Haute-Saone",
    "71": "Saone-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines",
    "79": "Deux-Sevres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne",
    "83": "Var", "84": "Vaucluse", "85": "Vendee", "86": "Vienne",
    "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Reunion", "976": "Mayotte",
}

# Department code to region mapping
DEPT_TO_REGION = {
    "75": "Ile-de-France", "77": "Ile-de-France", "78": "Ile-de-France",
    "91": "Ile-de-France", "92": "Ile-de-France", "93": "Ile-de-France",
    "94": "Ile-de-France", "95": "Ile-de-France",
    "13": "Provence-Alpes-Cote d'Azur", "83": "Provence-Alpes-Cote d'Azur",
    "84": "Provence-Alpes-Cote d'Azur", "04": "Provence-Alpes-Cote d'Azur",
    "05": "Provence-Alpes-Cote d'Azur", "06": "Provence-Alpes-Cote d'Azur",
    "69": "Auvergne-Rhone-Alpes", "01": "Auvergne-Rhone-Alpes",
    "03": "Auvergne-Rhone-Alpes", "07": "Auvergne-Rhone-Alpes",
    "15": "Auvergne-Rhone-Alpes", "26": "Auvergne-Rhone-Alpes",
    "38": "Auvergne-Rhone-Alpes", "42": "Auvergne-Rhone-Alpes",
    "43": "Auvergne-Rhone-Alpes", "63": "Auvergne-Rhone-Alpes",
    "73": "Auvergne-Rhone-Alpes", "74": "Auvergne-Rhone-Alpes",
    "31": "Occitanie", "09": "Occitanie", "11": "Occitanie",
    "12": "Occitanie", "30": "Occitanie", "32": "Occitanie",
    "34": "Occitanie", "46": "Occitanie", "48": "Occitanie",
    "65": "Occitanie", "66": "Occitanie", "81": "Occitanie", "82": "Occitanie",
    "33": "Nouvelle-Aquitaine", "16": "Nouvelle-Aquitaine",
    "17": "Nouvelle-Aquitaine", "19": "Nouvelle-Aquitaine",
    "23": "Nouvelle-Aquitaine", "24": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine", "47": "Nouvelle-Aquitaine",
    "64": "Nouvelle-Aquitaine", "79": "Nouvelle-Aquitaine",
    "86": "Nouvelle-Aquitaine", "87": "Nouvelle-Aquitaine",
    "44": "Pays de la Loire", "49": "Pays de la Loire",
    "53": "Pays de la Loire", "72": "Pays de la Loire", "85": "Pays de la Loire",
    "35": "Bretagne", "22": "Bretagne", "29": "Bretagne", "56": "Bretagne",
    "59": "Hauts-de-France", "02": "Hauts-de-France", "60": "Hauts-de-France",
    "62": "Hauts-de-France", "80": "Hauts-de-France",
    "67": "Grand Est", "68": "Grand Est", "10": "Grand Est",
    "08": "Grand Est", "51": "Grand Est", "52": "Grand Est",
    "54": "Grand Est", "55": "Grand Est", "57": "Grand Est",
    "88": "Grand Est",
    "21": "Bourgogne-Franche-Comte", "25": "Bourgogne-Franche-Comte",
    "39": "Bourgogne-Franche-Comte", "58": "Bourgogne-Franche-Comte",
    "70": "Bourgogne-Franche-Comte", "71": "Bourgogne-Franche-Comte",
    "89": "Bourgogne-Franche-Comte", "90": "Bourgogne-Franche-Comte",
    "76": "Normandie", "14": "Normandie", "27": "Normandie",
    "50": "Normandie", "61": "Normandie",
    "18": "Centre-Val de Loire", "28": "Centre-Val de Loire",
    "36": "Centre-Val de Loire", "37": "Centre-Val de Loire",
    "41": "Centre-Val de Loire", "45": "Centre-Val de Loire",
    "2A": "Corse", "2B": "Corse",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Reunion", "976": "Mayotte",
}

# Source configurations
SOURCES = {
    "cncef": {
        "name": "CNCEF",
        "full_name": "Chambre Nationale des Conseils Experts Financiers",
        "url": "https://www.cncef.org",
        "annuaire_url": "https://www.cncef.org/annuaire/",
        "ajax_url": "https://www.cncef.org/wp-admin/admin-ajax.php",
    },
    "cncgp": {
        "name": "CNCGP",
        "full_name": "Chambre Nationale des Conseillers en Gestion de Patrimoine",
        "url": "https://www.cncgp.fr",
        "annuaire_url": "https://www.cncgp.fr/annuaire",
    },
    "anacofi": {
        "name": "ANACOFI",
        "full_name": "Association Nationale des Conseils Financiers",
        "url": "https://www.anacofi.asso.fr",
        "export_url": "https://adherent.anacofi.asso.fr/action/export-societes-cif",
    },
    "orias": {
        "name": "ORIAS",
        "full_name": "Organisme pour le Registre des Intermediaires en Assurance",
        "url": "https://www.orias.fr",
        "search_url": "https://www.orias.fr/search",
    },
}

# Activity types
ACTIVITIES = [
    "CIF",        # Conseil en Investissements Financiers
    "COA",        # Courtier en Assurance
    "AGA",        # Agent General d'Assurance
    "MIA",        # Mandataire d'Intermediaire en Assurance
    "MA",         # Mandataire d'Assurance
    "IOBSP",      # Intermediaire en Operations de Banque et Services de Paiement
    "ALPSI",      # Agent Lie de Prestataire de Services d'Investissement
    "IFP",        # Intermediaire en Financement Participatif
    "Immobilier", # Conseil en Immobilier
]

# CGP Groupements reference data
GROUPEMENTS = [
    {
        "name": "Crystal / Laplace",
        "type": "plateforme",
        "website": "https://groupe-crystal.com/",
        "description": "Leader du marche, +30 acquisitions. Pole gestion privee Laplace.",
    },
    {
        "name": "Nortia (Groupe Apicil)",
        "type": "plateforme",
        "website": "https://www.nortia.fr/",
        "description": "Marketplace patrimoniale, 1000+ CGP partenaires, 13 Mds EUR d'encours.",
    },
    {
        "name": "UAF Life Patrimoine",
        "type": "plateforme",
        "website": "https://www.uaflifepatrimoine.fr/",
        "description": "Groupe Credit Agricole / Spirica.",
    },
    {
        "name": "Cyrus Conseil / Amplegest",
        "type": "groupement",
        "website": "https://www.cyrusconseil.fr/",
        "description": "Groupe Cyrus: Cyrus Conseil, Amplegest, Octo AM, Eternam.",
    },
    {
        "name": "Primonial / Laplace",
        "type": "plateforme",
        "website": "https://www.primonial.com/",
        "description": "Primonial Partenaires / L'Office by Primonial.",
    },
    {
        "name": "Consorteo",
        "type": "groupement",
        "website": "https://www.consorteo.fr/",
        "description": "Groupement de CGPI independants.",
    },
    {
        "name": "Generali Patrimoine",
        "type": "assureur",
        "website": "https://www.generali.fr/",
        "description": "Plateforme assureur pour CGP.",
    },
    {
        "name": "SwissLife Banque Privee",
        "type": "assureur",
        "website": "https://www.swisslife-am.com/",
        "description": "Canal CGPI SwissLife.",
    },
    {
        "name": "Abeille Assurances (ex-Aviva)",
        "type": "assureur",
        "website": "https://www.abeille-assurances.fr/",
        "description": "Plateforme assureur.",
    },
    {
        "name": "Cardif / BNP Paribas",
        "type": "assureur",
        "website": "https://www.cardif.fr/",
        "description": "Plateforme assurance vie CGPI.",
    },
    {
        "name": "Groupe DLPK / Vie Plus",
        "type": "plateforme",
        "website": "https://www.dlpk.fr/",
        "description": "Courtier grossiste, plateforme Vie Plus.",
    },
    {
        "name": "Patrimea",
        "type": "plateforme",
        "website": "https://www.patrimea.com/",
        "description": "Plateforme digitale CGP.",
    },
    {
        "name": "UCGP",
        "type": "groupement",
        "website": "https://www.ucgp.fr/",
        "description": "Union des Conseils en Gestion de Patrimoine.",
    },
]

# Associations professionnelles
ASSOCIATIONS = [
    {
        "key": "cncgp",
        "name": "CNCGP",
        "full_name": "Chambre Nationale des Conseillers en Gestion de Patrimoine",
        "website": "https://www.cncgp.fr",
        "members_approx": 4200,
        "description": "Creee en 1978. 6900 adherents individuels, 4200 cabinets.",
    },
    {
        "key": "cncef",
        "name": "CNCEF",
        "full_name": "Chambre Nationale des Conseils Experts Financiers",
        "website": "https://www.cncef.org",
        "members_approx": 4800,
        "description": "Syndicat professionnel depuis 1957. CNCEF Patrimoine, CNCEF Credit, CNCEF Assurance, CNCEF Immobilier.",
    },
    {
        "key": "anacofi",
        "name": "ANACOFI",
        "full_name": "Association Nationale des Conseils Financiers",
        "website": "https://www.anacofi.asso.fr",
        "members_approx": 2750,
        "description": "Creee en 2004. Plus grande association de CIF en France. 12 000+ professionnels.",
    },
    {
        "key": "affo",
        "name": "AFFO",
        "full_name": "Association Francaise du Family Office",
        "website": "https://www.affo.fr",
        "members_approx": 120,
        "description": "Creee en 2001. Multi-family offices, single family offices et experts.",
    },
]
