import json
import requests
import io
import re

from dateutil.parser import parse
from time import gmtime, strftime

def get_dataset_dois(references):

    dois_referenced = []
    
    for r in references:
        if 'dois' in r['reference']:
            if re.search('OPENDATA.CMS', r['reference']['dois'][0], re.IGNORECASE):
                doi = r['reference']['dois'][0]
                dois_referenced.append(doi)

    return dois_referenced

def handle_publication_info(info):

    if 'publication_info' in info:

        publication_info = info['publication_info']
        
        if 'journal_title' in publication_info[0]:
            return publication_info[0]['journal_title']
        else:
            return ''
    else:
        return ''
    
inspire_url = 'https://inspirehep.net/api'

'''
Get the information on papers that contain 
CMS open data DOIs in the references
'''

record_type = 'literature'

search_query = 'q=references.reference.dois:10.7483/OPENDATA.CMS*'
sort_order = 's=mostrecent'
n_results = 'size=500'

query_string = f'{n_results}&{sort_order}&{search_query}'

query = f'{inspire_url}/{record_type}?{query_string}'

response = requests.get(query, timeout=300, stream=True)
results = response.json()

papers = []
ofile = open('inspire.json', 'w')

hits = results['hits']['hits']

for hi, hit in enumerate(hits):
    
    metadata = hit['metadata']
    
    title = metadata['titles'][0]['title']
    date = str(parse(hit['created']).date())
    hid = metadata['control_number']
    abstract = metadata['abstracts'][0]['value']

    try:
        authors = [a['full_name'] for a in metadata['authors']]
    except KeyError:
        authors = []
        
    document_type = metadata['document_type'][0]
    publication_info = handle_publication_info(metadata)        
    dois_referenced = get_dataset_dois(metadata['references'])
    citations = metadata['citation_count']
    
    try: 
        keywords = [m['value'] for m in metadata['keywords']]
    except KeyError:
        keywords = []
    
    try:
        doi = metadata['dois'][0]['value']
    except KeyError:
        doi = ''

    try:
        collaborations = [c['value'] for c in metadata['collaborations']]
    except KeyError:
        collaborations = []
        
    obj = {}

    obj['title'] = title
    obj['abstract'] = abstract
    obj['date'] = date
    obj['url'] = f'https://inspirehep.net/literature/{hid}'
    obj['doi'] = doi
    obj['authors'] = authors
    obj['keywords'] = keywords
    obj['dois_referenced'] = dois_referenced
    obj['document_type'] = document_type
    obj['publication'] = publication_info
    obj['citations'] = citations
    obj['collaborations'] = collaborations
    
    papers.append(obj)

ofile.write(
    json.dumps(
        papers,
        sort_keys=True,
        indent=4
    )
)

ofile.close()
