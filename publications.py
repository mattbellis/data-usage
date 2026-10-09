import json
import re
import subprocess
import sys
import datetime as dt

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from wordcloud import WordCloud, STOPWORDS


data_releases = [
    '2014-11-20',
    '2016-04-22',
    '2017-12-20',
    '2019-07-18',
    '2020-08-27',
    '2020-12-21',
    '2021-12-20',
    '2022-12-05',
    '2023-09-18',
    '2024-04-02',
]


class Tee:
    """
    Write everything printed to both the terminal and a log file
    """
    def __init__(self, path):
        self.terminal = sys.stdout
        self.log = open(path, 'w')

    def write(self, text):
        self.terminal.write(text)
        self.log.write(text)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        sys.stdout = self.terminal
        self.log.close()


def print_progress(i, n):
    """
    Progress counter that updates in place in the terminal;
    only the final count is written to the log
    """
    line = f'  {i}/{n} ({i/n*100:.0f}%)'
    terminal = getattr(sys.stdout, 'terminal', sys.stdout)

    print(line, end='\r' if i < n else '\n', flush=True, file=terminal)

    if i == n and isinstance(sys.stdout, Tee):
        sys.stdout.log.write(line + '\n')


def make_wordcloud(df, column_name, ftitle):
    print(f'  Generating word cloud ({column_name}) -> figs/{ftitle}.png')
    text = ' '.join(df[column_name].values)

    exclude = [
        'using', 'CMS', 'open', 'data', 'collider', 'event', 'TeV',
        'analysis', 'based', 'LHC', 'particle', 'end', 'high', 'energy',
        'physics', 'new', 'experiment',
    ]

    stopwords = set(STOPWORDS)
    for e in exclude:
        stopwords.add(e)

    wc = WordCloud(
        background_color='white',
        stopwords=stopwords,
        width=600,
        height=400,
    ).generate(text)

    plt.figure()
    plt.imshow(wc, interpolation='bilinear')
    plt.axis('off')
    plt.savefig(f'./figs/{ftitle}.png', dpi=200)
    plt.close()


def handle_doi_queries(dois):
    doi_url = 'https://doi.org/api/handles/'
    recids = []

    for doi in dois:
        if ':' in doi:
            continue
        if '(' in doi:
            doi = doi[:-6]

        try:
            response = requests.get(f'{doi_url}{doi}').json()
            url = response['values'][1]['data']['value']
            recid = url.split('/')[-1]
            recids.append(recid)
        except KeyError:
            print('Error ' + doi)

    return recids


def resolve_dois(df):
    df['codp_recids'] = df['dois_referenced'].map(
        lambda x: handle_doi_queries(x)
    )


def process_dataframe(input_json):
    exclude_names = [
        'McCauley', 'Bellis', 'Lange', 'Tibor', 'Šimko', 'Carrera',
        'Geiser', 'Lassila-Perini', 'Dallmeier-Tiessen', 'Calderon',
        'Rao, A', 'Socher', 'Herterich', 'Hogan', 'Saiz',
    ]

    df = pd.read_json(input_json)

    df['exclude'] = df['authors'].map(
        lambda x: [e for e in exclude_names if any(e.lower() in xn.lower() for xn in x)]
    )

    print(f'  {df.shape[0]} papers before author filter')

    df = df[df['exclude'].str.len() == 0]

    print(f'  {df.shape[0]} papers after author filter')

    df.sort_values(by='date', inplace=True)
    df.reset_index(drop='True', inplace=True)
    df.reset_index(inplace=True)

    df['date'] = pd.to_datetime(df['date'], format='%Y-%m-%d')

    return df


POLICY_DOI = '10.7483/opendata.cms.1bnu.8v1w'


def exclude_policy_only(df):
    """
    Drop papers whose only CMS open data DOI reference is the
    data policy document
    """
    policy_only = df['dois_referenced'].map(
        lambda x: {d.lower() for d in x} == {POLICY_DOI}
    )

    print(f'  {policy_only.sum()} papers citing only the policy DOI excluded')

    df = df[~policy_only].copy()
    df.reset_index(drop=True, inplace=True)
    df['index'] = df.index

    return df


def exclude_cms_collaboration(df):
    """
    Drop papers from the CMS Collaboration
    """
    if 'collaborations' not in df.columns:
        print('  No collaborations field: re-run data/query_inspire.py')
        return df

    cms = df['collaborations'].map(
        lambda x: any(c.upper() == 'CMS' for c in x)
    )

    print(f'  {cms.sum()} CMS Collaboration papers excluded')

    df = df[~cms].copy()
    df.reset_index(drop=True, inplace=True)
    df['index'] = df.index

    return df


def make_histogram(df, date_range, nbins, title, ftitle):
    print(f'  Generating histogram -> figs/{ftitle}.png')
    dates = df['date'].to_numpy(dtype='datetime64[Y]')

    h, b = np.histogram(
        dates.astype(int),
        range=date_range,
        bins=nbins,
    )

    b = np.array([np.datetime64(int(value), 'Y') for value in b])

    plt.figure()
    plt.bar(b[:-1], h, width=np.diff(b), ec='black', align='edge')
    plt.gca().set_xticks(b)
    plt.gca().set_xticklabels(b, rotation=45)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(f'./figs/{ftitle}.png', dpi=200)
    plt.close()


def get_codp_title(recid):
    results = subprocess.run(
        ['cernopendata-client', 'get-metadata', '--recid', recid,
         '--output-value', 'title'],
        stdout=subprocess.PIPE,
    )
    return results.stdout.decode('utf-8')


def get_codp_categories(recid):
    results = subprocess.run(
        ['cernopendata-client', 'get-metadata', '--recid', recid,
         '--output-value', 'categories'],
        stdout=subprocess.PIPE,
    )
    results = results.stdout.decode('utf-8')

    if 'ERROR' in results:
        return '', []

    categories = json.loads(results)
    return categories['primary'], categories['secondary']


def main():
    date_generated = dt.datetime.today().strftime('%Y-%m-%d')
    print(f'Date: {date_generated}')

    # --- Inspire ---
    print('\nProcessing Inspire data...')
    idf = process_dataframe('data/inspire.json')
    idf = exclude_policy_only(idf)
    idf = exclude_cms_collaboration(idf)

    print('Resolving DOIs (this may take a while)...')
    resolve_dois(idf)

    print('Saving figs/inspire-npapers.png')
    ax = idf.plot(
        kind='scatter',
        x='date',
        y='index',
    )
    ax.set_xlabel('Date published', fontsize=14)
    ax.set_ylabel('Number of papers', fontsize=14)
    ax.set_title(
        f'Papers citing CMS Open Data DOIs or CODP urls [Inspire]\n'
        f'Excluding CMS/CERN open data teams and CMS Collaboration\n{date_generated}',
        fontsize=10,
    )
    plt.xticks(fontsize=12)
    plt.yticks(fontsize=12)
    plt.savefig('./figs/inspire-npapers.png', dpi=200)
    plt.close()

    make_histogram(
        idf,
        (np.datetime64('2017').astype(int), np.datetime64('2027').astype(int)),
        10,
        f'Papers citing CMS Open Data DOIs or CODP urls [Inspire]\n'
        f'Excluding CMS/CERN open data teams and CMS Collaboration\n{date_generated}',
        'inspire-npapers-hist',
    )

    nzero = len(idf[idf['citations'] == 0])
    nzdf = idf[idf['citations'] > 0]
    print(f'  {nzero} papers with 0 citations')
    print(f'  {len(nzdf)} papers with > 0 citations')

    h, b = np.histogram(nzdf['citations'], range=(0, 200), bins=20)

    plt.figure()
    plt.bar(b[:-1], h, width=np.diff(b), ec='black', align='edge')
    plt.gca().set_xticks(b)
    plt.xticks(fontsize=12)
    plt.gca().set_xticklabels(b.astype(int), rotation=45)
    plt.yticks(fontsize=12)
    plt.gca().set_yscale('log')
    plt.title(
        f'Number of citations of papers citing CMS Open Data DOIs or CODP urls [Inspire]\n'
        f'{nzero} papers with 0 citations excluded\n{date_generated}'
    )
    plt.tight_layout()
    print('Saving figs/inspire-citations.png')
    plt.savefig('./figs/inspire-citations.png', dpi=200)
    plt.close()

    make_wordcloud(idf, 'title', 'inspire-wc-title')
    make_wordcloud(idf, 'abstract', 'inspire-wc-abstract')

    print('Fetching CODP titles (this may take a while)...')
    n = len(idf)
    titles = []
    for i, recids in enumerate(idf['codp_recids']):
        titles.append([get_codp_title(str(rid)) for rid in recids])
        print_progress(i+1, n)

    print('Fetching CODP categories (this may take a while)...')
    categories = []
    for i, recids in enumerate(idf['codp_recids']):
        categories.append([get_codp_categories(str(rid)) for rid in recids])
        print_progress(i+1, n)

    primary_categories = []
    secondary_categories = []

    for c in categories:
        if len(c) > 0:
            for d in c:
                if d[0]:
                    primary_categories.append(d[0])
                if d[1]:
                    for e in d[1]:
                        secondary_categories.append(e)

    print(f'  {len(primary_categories)} primary categories, {len(secondary_categories)} secondary categories')

    print('Saving figs/inspire-dataset-primary-categories.png')
    pd.Series(primary_categories).value_counts(sort=False).plot(
        kind='barh',
        title=f'Primary categories of datasets cited [Inspire]\n{date_generated}',
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-dataset-primary-categories.png', dpi=200)
    plt.close()

    print('Saving figs/inspire-dataset-secondary-categories.png')
    pd.Series(secondary_categories).value_counts(sort=False).plot(
        kind='barh',
        title=f'Secondary categories of datasets cited [Inspire]\n{date_generated}',
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-dataset-secondary-categories.png', dpi=200)
    plt.close()

    print('Parsing dataset names from titles...')
    titles = [[t.rstrip() for t in title] for title in titles]

    dataset_names = []
    dataset_eras = []
    dataset_tiers = []
    others = []

    for title in titles:
        for t in title:
            if len(re.findall('/', t)) == 3:
                t = t.split('/')
                if 'Run201' in t[2]:
                    dataset_eras.append(t[2].split('-')[0])
                dataset_names.append(t[1])
                dataset_tiers.append(t[3])
            else:
                others.append(t)

    print(f'  {len(dataset_names)} dataset names, {len(dataset_eras)} eras, {len(dataset_tiers)} tiers')

    print('Saving figs/inspire-datatiers.png')
    pd.Series(dataset_tiers).value_counts(sort=False).plot(
        kind='barh',
        title=f'Data tiers cited [Inspire]\n{date_generated}',
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-datatiers.png', dpi=200)
    plt.close()

    print('Saving figs/inspire-dataset-eras.png')
    pd.Series(dataset_eras).value_counts(sort=False).plot(
        kind='barh',
        title=f'Dataset eras cited [Inspire]\n{date_generated}',
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-dataset-eras.png', dpi=200)
    plt.close()

    print('Saving figs/inspire-dataset-names.png')
    pd.Series(dataset_names).value_counts(sort=True).plot(
        kind='barh',
        title=f'Dataset names cited [Inspire]\n{date_generated}',
        figsize=(12, 20),
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-dataset-names.png', dpi=200)
    plt.close()

    print('Saving figs/inspire-dataset-names-groups.png')
    dsn = pd.Series(dataset_names)
    groups = dsn.str.split('_').str[0] + '_*'
    groups.value_counts(sort=True).plot(
        kind='barh',
        title=f'Dataset names cited [Inspire]\n{date_generated}',
        figsize=(12, 20),
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-dataset-names-groups.png', dpi=200)
    plt.close()

    print('Saving figs/inspire-publications.png')
    publications = idf['publication'].values
    publications = [p for p in publications if p]
    pd.Series(publications).value_counts(sort=True).plot(
        kind='barh',
        title=f'Publications [Inspire]\n{date_generated}',
        figsize=(12, 20),
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-publications.png', dpi=200)
    plt.close()

    print('Saving figs/inspire-publication-type.png')
    idf['document_type'].value_counts(sort=False).plot(
        kind='barh',
        title=f'Publication type [Inspire]\n{date_generated}',
    )
    plt.tight_layout()
    plt.savefig('./figs/inspire-publication-type.png', dpi=200)
    plt.close()

    # --- arXiv ---
    print('\nProcessing arXiv data...')
    adf = process_dataframe('data/arxiv.json')

    print('Saving figs/arxiv-npapers.png')
    ax = adf.plot(
        kind='scatter',
        x='date',
        y='index',
        title=f'Papers containing "CMS Open Data" in the abstract [arXiv]\n{date_generated}',
    )
    ax.set_xlabel('Date published')
    ax.set_ylabel('Number of papers')
    plt.tight_layout()
    plt.savefig('./figs/arxiv-npapers.png', dpi=200)
    plt.close()

    make_histogram(
        adf,
        (np.datetime64('2017').astype(int), np.datetime64('2026').astype(int)),
        9,
        f'Papers containing "CMS Open Data" in the abstract [arXiv]\n{date_generated}',
        'arxiv-npapers-hist',
    )

    make_wordcloud(adf, 'title', 'arxiv-wc-title')
    make_wordcloud(adf, 'abstract', 'arxiv-wc-abstract')

    print('\nDone.')


if __name__ == '__main__':
    log = Tee('publications.log')
    sys.stdout = log
    try:
        main()
    finally:
        log.close()
