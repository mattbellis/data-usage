import json
import requests
import io
import re
import time
import logging
import tempfile
import os

from pypdf import PdfReader

from dateutil.parser import parse
from time import gmtime, strftime

def get_dataset_dois(references):

    dois_referenced = []
    
    for r in references:
        reference = r.get('reference', {})
        for doi in reference.get('dois', []):
            if re.search('OPENDATA.CMS', doi, re.IGNORECASE) and doi not in dois_referenced:
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

'''
CMS open data DOIs, also when a PDF's text breaks them
across lines (e.g. "OPENDATA.CMS .GV20.PR5T")
'''
cms_doi_pattern = re.compile(r'10\.7483/OPENDATA\.CMS\.[A-Z0-9]{4}\.[A-Z0-9]{4}', re.IGNORECASE)

logging.getLogger('pypdf').setLevel(logging.ERROR)

def get_pdf_dois(arxiv_id):

    '''
    CMS open data DOIs in the arXiv PDF of a paper, from both the
    link annotations (DOIs shown as e.g. a dataset name linked to
    doi.org) and the text (DOIs printed in references or tables)
    '''
    dois = set()

    with tempfile.TemporaryDirectory() as tmp:
        pdf = f'{tmp}/{arxiv_id.replace("/", "_")}.pdf'

        try:
            response = requests.get(f'https://arxiv.org/pdf/{arxiv_id}', timeout=300)
            response.raise_for_status()
            with open(pdf, 'wb') as f:
                f.write(response.content)
            reader = PdfReader(pdf)

            for page in reader.pages:
                for annot in page.get('/Annots') or []:
                    uri = (annot.get_object().get('/A') or {}).get('/URI')
                    if uri:
                        dois.update(cms_doi_pattern.findall(str(uri)))

                text = page.extract_text() or ''
                dois.update(cms_doi_pattern.findall(text))
                dois.update(cms_doi_pattern.findall(re.sub(r'\s+', '', text)))

        except Exception as e:
            print(f'Error reading arXiv PDF {arxiv_id}: {e}')

    '''
    arXiv asks for a pause between automated requests
    '''
    time.sleep(3)

    return sorted({d.upper() for d in dois})

'''
Authors (CMS/CERN open data teams) whose papers are excluded,
shared with publications.py
'''
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exclude_names.json')) as f:
    exclude_names = json.load(f)

def passes_filters(metadata):

    '''
    The author and CMS Collaboration filters from publications.py,
    used to only read the PDFs of fulltext papers that will be kept
    '''
    collaborations = [c['value'].upper() for c in metadata.get('collaborations', [])]
    authors = [a['full_name'].lower() for a in metadata.get('authors', [])]

    if 'CMS' in collaborations:
        return False

    return not any(e.lower() in a for e in exclude_names for a in authors)

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

def search_inspire(search_query):

    params = {'q': search_query, 's': 'mostrecent', 'size': 500}

    response = requests.get(f'{inspire_url}/{record_type}', params=params, timeout=300)
    results = response.json()
    hits = results['hits']['hits']

    if results['hits']['total'] > len(hits):
        print(f'Warning: {results["hits"]["total"]} results for {search_query}, only {len(hits)} fetched')

    return hits

reference_query = 'references.reference.dois:10.7483/OPENDATA.CMS* or references.reference.urls.value:*opendata.cern.ch*'
fulltext_query = 'fulltext:"10.7483/OPENDATA.CMS" and not collaboration:CMS'

hits = search_inspire(reference_query)
reference_ids = {hit['metadata']['control_number'] for hit in hits}

'''
Papers with CMS open data DOIs somewhere in their text (e.g. in a
table, or references INSPIRE did not parse). For those whose references
give no CMS DOIs or records (whether or not the reference search finds
them), the DOIs are taken from the arXiv PDF.
'''
fulltext_results = search_inspire(fulltext_query)
fulltext_ids = {hit['metadata']['control_number'] for hit in fulltext_results}
fulltext_hits = [hit for hit in fulltext_results if hit['metadata']['control_number'] not in reference_ids]

'''
Only read the PDFs of fulltext papers that pass the filters; the
others are still written out but without the DOIs from the PDF
'''
fulltext_to_read = {hit['metadata']['control_number'] for hit in fulltext_results if passes_filters(hit['metadata'])}

print(f'{len(hits)} papers from the reference search, {len(fulltext_hits)} more from the fulltext search')
print(f'{len(fulltext_to_read)} fulltext papers pass the author and CMS Collaboration filters')

n_pdfs = 0

papers = []
ofile = open('inspire.json', 'w')

for hi, hit in enumerate(hits + fulltext_hits):
    
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

    dois_from_pdf = False

    if hid in fulltext_to_read and not dois_referenced and not recids_referenced:
        try:
            arxiv_id = metadata['arxiv_eprints'][0]['value']
            n_pdfs += 1
            print(f'Reading arXiv PDF {arxiv_id} ({n_pdfs})')
            record_dois += get_pdf_dois(arxiv_id)
            dois_from_pdf = True
        except KeyError:
            print(f'No arXiv PDF for https://inspirehep.net/literature/{hid}')

    for d in record_dois:
        if d.lower() not in [x.lower() for x in dois_referenced]:
            dois_referenced.append(d)

    '''
    Skip papers that cite no CMS open data DOI and no CMS Dataset
    record (e.g. only the portal, docs pages, or other experiments).
    Fulltext papers are kept, since their text has a CMS open data DOI.
    '''
    if hid not in fulltext_ids and not dois_referenced and not recids_referenced:
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
    obj['dois_from_pdf'] = dois_from_pdf
    
    papers.append(obj)

ofile.write(
    json.dumps(
        papers,
        sort_keys=True,
        indent=4
    )
)

ofile.close()
