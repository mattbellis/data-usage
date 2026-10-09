import json
import requests
import io
import re

from dateutil.parser import parse
from time import gmtime, strftime

def get_dataset_dois(references):

    dois_referenced = []
    
    for r in references:
        reference = r.get('reference', {})
        if 'dois' in reference:
            if re.search('OPENDATA.CMS', reference['dois'][0], re.IGNORECASE):
                doi = reference['dois'][0]
                dois_referenced.append(doi)

    return dois_referenced

def get_record_ids(references):

    '''
    CERN Open Data Portal record ids from opendata.cern.ch/record/<id>
    URLs in the references
    '''
    recids_referenced = []

    for r in references:
        for u in r.get('reference', {}).get('urls', []):
            m = re.search(r'opendata\.cern\.ch/record/(\d+)', u['value'], re.IGNORECASE)
            if m and m.group(1) not in recids_referenced:
                recids_referenced.append(m.group(1))

    return recids_referenced

codp_url = 'https://opendata.cern.ch/api/records'
codp_records = {}

def get_codp_record(recid):

    '''
    Metadata of a CERN Open Data Portal record, cached since
    many papers cite the same records
    '''
    if recid not in codp_records:
        try:
            response = requests.get(
                f'{codp_url}/{recid}',
                headers={'Accept': 'application/json'},
                timeout=300
            )
            codp_records[recid] = response.json()['metadata']
        except (requests.RequestException, ValueError, KeyError):
            print(f'Error looking up record {recid}')
            codp_records[recid] = {}

    return codp_records[recid]

def get_cms_dataset_records(references):

    '''
    Record ids and DOIs of the CMS Dataset records cited
    by CERN Open Data Portal URLs in the references
    '''
    recids = []
    dois = []

    for recid in get_record_ids(references):
        record = get_codp_record(recid)

        if 'CMS' in record.get('experiment', []) and \
           record.get('type', {}).get('primary') == 'Dataset':
            recids.append(recid)

            if record.get('doi'):
                dois.append(record['doi'])

    return recids, dois

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
CMS open data DOIs or CERN Open Data Portal URLs
in the references
'''

record_type = 'literature'

search_query = 'q=references.reference.dois:10.7483/OPENDATA.CMS* or references.reference.urls.value:*opendata.cern.ch*'
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

    try:
        abstract = metadata['abstracts'][0]['value']
    except KeyError:
        abstract = ''

    try:
        authors = [a['full_name'] for a in metadata['authors']]
    except KeyError:
        authors = []
        
    document_type = metadata['document_type'][0]
    publication_info = handle_publication_info(metadata)        
    references = metadata.get('references', [])
    dois_referenced = get_dataset_dois(references)
    recids_referenced, record_dois = get_cms_dataset_records(references)

    for d in record_dois:
        if d.lower() not in [x.lower() for x in dois_referenced]:
            dois_referenced.append(d)

    '''
    Skip papers that cite no CMS open data DOI and no CMS Dataset
    record (e.g. only the portal, docs pages, or other experiments)
    '''
    if not dois_referenced and not recids_referenced:
        continue

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
    obj['recids_referenced'] = recids_referenced
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
