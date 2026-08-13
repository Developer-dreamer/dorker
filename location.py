import re


def evaluate_location_relevance(location_str: str) -> str:
    """
    Analyzes a location string based on a real-world dataset of 3.7M vacancies.
    Rejects raw non-remote geographic data and filters localized exclusions.
    """
    if not location_str:
        return "REJECT_EMPTY"

    # 1. STANDARDIZATION
    location_raw = location_str.strip()
    location = location_raw.lower()

    # ==========================================
    # STEP 1: CONTEXT SECURITY GATE (Eliminate pure On-Site/Hybrid data)
    # ==========================================
    # If the string doesn't say "remote" or "wfh", it's an office job city/hub. Reject it.
    remote_context_tokens = ['remote', 'wfh', 'home', 'anywhere', 'worldwide', 'world wide', 'global', 'emea', 'europe']
    if not any(token in location for token in remote_context_tokens):
        return "REJECT_NO_REMOTE_CONTEXT"

    global_markers = ['worldwide', 'global', 'world wide', 'emea', 'europe', 'international', 'latam', 'apac', 'utc'] # maybe should add 'amer' but not for me obviously
    if any(re.search(r'\b' + re.escape(w) + r'\b', location) for w in global_markers) or re.search(r'\b(eu|world)\b', location):
        return "KEEP_GLOBAL"

    # ==========================================
    # STEP 2: HARD EXCLUSIONS (Negative Filters First)
    # ==========================================

    # A. US Specific Phrases & Cities
    us_specific_words = [
        'united states', 'america', 'usa', 'u.s.', 'nationwide', 'any state', 'san francisco',
        'sanfrancisco', 'sf', 'austin', 'boston', 'chicago', 'seattle', 'atlanta', 'new york', 'nyc',
        'los angeles', 'la', 'silicon valley', 'bay area', 'bayarea', 'socal', 'southern california',
        'denver', 'dallas', 'houston', 'miami', 'philadelphia', 'phoenix', 'portland', 'noram', 'us_remote',
        'columbus', 'leawood', 'dtla', 'maryland', 'sunnyvale', 'redwood city', 'mclean', 'arlington', 'san diego'
    ]
    if any(w in location for w in us_specific_words) or re.search(r'\b(us|usa|u\.s|sf|nyc|la)\b', location) or location.endswith('-united-states'):
        return "EXCLUDE_US"

    # B. US Case-Sensitive 2-Letter State Codes
    state_codes = [
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA',
        'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT',
        'VA', 'WA', 'WV', 'WI', 'WY', 'PR', 'DC', 'OH'
    ]
    if re.search(r'\b(' + '|'.join(state_codes) + r')\b', location_raw):
        return "EXCLUDE_US_STATE"

    # C. Full US State Names
    us_states_full = [
        'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado', 'connecticut', 'delaware',
        'florida', 'georgia', 'hawaii', 'idaho', 'illinois', 'indiana', 'iowa', 'kansas', 'kentucky',
        'louisiana', 'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota', 'mississippi',
        'missouri', 'montana', 'nebraska', 'nevada', 'new hampshire', 'new jersey', 'new mexico',
        'new york', 'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania',
        'rhode island', 'south carolina', 'south dakota', 'tennessee', 'texas', 'utah', 'vermont',
        'virginia', 'washington', 'west virginia', 'wisconsin', 'wyoming', 'socal', 'south california', 'bay area'
    ]
    if any(w in location for w in us_states_full):
        return "EXCLUDE_US_STATE"

    # D. Non-compatible International Hubs & Countries (Including common spelling variations)
    other_countries_and_hubs = [
        'brazil', 'brasil', 'mexico', 'canada', 'india', 'germany', 'deutschland', 'united kingdom', 'london', 'uk', 'gb',
        'spain', 'philippines', 'colombia', 'poland', 'ireland', 'france', 'australia', 'singapore', 'japan',
        'china', 'netherlands', 'sweden', 'switzerland', 'austria', 'belgium', 'denmark', 'norway', 'finland',
        'argentina', 'portugal', 'serbia', 'romania', 'turkey', 'türkiye', 'slovakia', 'hungary', 'costa rica',
        'chile', 'paris', 'ontario', 'toronto', 'vancouver', 'sydney', 'melbourne', 'berlin', 'munich', 'amsterdam',
        'pakistan', 'italy', 'taiwan', 'south korea', 'cyprus', 'bulgaria', 'vietnam', 'malaysia', 'armenia', 'indonesia',
        'bangalore', 'manila', 'münchen', 'hamburg', 'düsseldorf', 'são paulo', 'sao paulo', 'bogota', 'madrid', 'barcelona',
        'south africa', 'dubai', 'lyon', 'bangkok', 'thailand', 'abuja', 'nigeria', 'seoul', 'derbyshire', 'guatemala', 'leeds',
        'sharjah', 'ae', 'boisbriand', 'taupo', 'home counties', 'manchester', 'birmingham', 'scotland', 'wales',
        'islamabad', 'lahore', 'stellenbosch', 'nürnberg', 'münster', 'kiel', 'bremen', 'ravensburg', 'rankweil', 'reading',
        'zagreb', 'pula', 'lisbon', 'zurich', 'mannheim', 'villach', 'bonn', 'freiburg', 'cork', 'suhl', 'essen', 'fulda',
        'hannover', 'mönchengladbach', 'österreich', 'schweiz', 'neuss', 'mainz', 'stuttgart', 'köln', 'halle', 'leipzig',
        'heidenheim', 'würzburg', 'kassel', 'gießen', 'ludwigshafen', 'schwandorf', 'hengersberg', 'plattlingen', 'karlsruhe',
        'gronau', 'bad hersfeld', 'ingolstadt', 'duisburg', 'neumünster', 'celle', 'minden', 'wolfsbrug', 'göttingen', 'trier',
        'heilbronn', 'ulm', 'augsburg', 'kempten', 'garmisch', 'passau', 'rosenheim', 'fürth', 'landshut', 'burg', 'stendal',
        'colbitz', 'schönebeck', 'zerbst', 'königsborn', 'zeppernick', 'lübars', 'magdeburg', 'bernau', 'eberswalde', 'strausberg',
        'velten', 'potsdam', 'fürstenwalde', 'bremerhaven', 'kaiserslautern', 'saarbrücken', 'salzburg', 'kufstein', 'gelsenkirchen',
        'rendsburg', 'aschaffenburg', 'meiningen', 'koblenz', 'gera', 'aichstetten', 'allgäu', 'lindau', 'stendell', 'gramzow',
        'caselow', 'randowtal', 'schwedt', 'temmen', 'ringenwalde', 'gerswalde', 'joachimsthal', 'prenzlau', 'lunow', 'stolzenhagen',
        'görlitz', 'pinnow', 'penkun', 'tantow', 'angermünde', 'horka', 'dresden', 'chemnitz', 'dortmund', 'salzgitter', 'iserlohn',
        'aachen', 'new delhi', 'tokyo', 'taipei', 'schleswig-holstein', 'tpg zentrale', 'markkleeberg'
    ]
    if any(c in location for c in other_countries_and_hubs):
        return "EXCLUDE_OTHER_COUNTRY"

    # E. ISO 2-letter Country Codes
    country_iso2 = ['br', 'mx', 'ca', 'in', 'de', 'uk', 'es', 'pl', 'fr', 'au', 'sg', 'ie', 'pt', 'tr', 'ro', 'rs', 'pk', 'it', 'tw', 'kr', 'cy', 'bg', 'vn', 'my', 'am', 'id', 'co', 'za', 'lk', 'cn', 'cz', 'th', 'ae', 'ng', 'gt']
    if re.search(r'\b(' + '|'.join(country_iso2) + r')\b', location):
        return "EXCLUDE_OTHER_COUNTRY"

    # F. ISO 3-letter Country Prefixes
    if re.search(r'\b(ind|bra|bgr|arg|deu|aus|can|usa|phl|gbr|fra|esp|mex|col|prt|tha|are|nga|gtm)\b', location):
        return "EXCLUDE_OTHER_COUNTRY"

    # ==========================================
    # STEP 3: INCLUSIONS (Positive Filters Later)
    # ==========================================

    if re.search(r'\b' + re.escape('anywhere') + r'\b', location):
        return "KEEP_GLOBAL"

    # 1. TARGETED LOCAL REMOTE (Highest Positive Priority)
    ukraine_markers = ['ukraine', 'kyiv', 'kiev', 'lviv', 'odessa', 'kharkiv', 'dnipro']
    if any(w in location for w in ukraine_markers) or re.search(r'\bua\b', location):
        return "KEEP_LOCAL"

    # 3. PURE / GENERIC REMOTE
    generic_remotes = {
        'remote', 'remote job', 'remote position', 'remote location', 'any location / remote',
        'remote/homebased', 'remote locations', '1 remote', 'homebased', 'fully remote',
        'remotely based', 'remote office', '100% remote', 'field/remote', 'remote worker - wfh',
        'remote home office', 'remote worker', 'work from home', 'wfh', 'remote; work from home'
    }
    if location in generic_remotes or location.replace(' ', '') == '100%remote':
        return "KEEP_PURE"

    # Explicitly catch strings that say "hybrid" unless they also match local criteria later
    if ('hybrid' in location
            and not any(m in location for m in ['ukraine', 'kyiv', 'kiev', 'lviv'])
            and 'remote' not in location):
        return "REJECT_HYBRID_NON_LOCAL"

    return "POTENTIAL_PURE"

    check = ["Bay Area (hybrid/remote)",
             "Remote or Hybrid",
             "Hybrid / Remote",
             "Hybrid / Remote first",
             "Hybrid/Remote",
             "Remote/Hybrid (SoCal)",
             "Hybrid or Remote",
             "Remote/Hybrid",
             "Remote/ Hybrid-Bay Area",
             "Bay Area / Hybrid / Remote",
             "Hybrid SanFrancisco, or remote outside of SF",
             "Hybrid Remote - Eastern or Central Time Zones",
             "On-Site, Hybrid, or Remote",
             "On-site/ Hybrid / Remote",
             "Remote, or Hybrid SF, NYC, BOS or CHI",
             "Hybrid - DTLA & Remote",
             "Remote,Hybrid",
             "flowit AG (Hybrid),Remote",
             "Hybrid,Remote - Schleswig-Holstein",
             "TPG Zentrale,Hybrid,Remote",
             "Markkleeberg,Hybrid,Remote",
             "On-site / hybrid / remote",
             "On-site / Hybrid / Remote"
             ]
    for i in range(0, len(check)):
        res = evaluate_location_relevance(check[i])
        print(f"{str.lower(check[i])}: {res}")
