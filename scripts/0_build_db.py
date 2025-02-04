#!/usr/bin/env python3
# Copyright 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
"""A script to read in and store documents in a sqlite database."""

from unicodedata import normalize as unicodedata_normalize
from argparse import ArgumentParser
from json import loads as json_loads
from os import walk as os_walk
from os.path import isfile as os_path_isfile, isdir as os_path_isdir, join as os_path_join
from multiprocessing import Pool
from logging import getLogger, INFO, Formatter, StreamHandler
from importlib.util import spec_from_file_location, module_from_spec
from bz2 import open as bz2_open
from sqlite3 import connect as sqlite3_connect
from pickle import dumps as pickle_dumps
from tqdm import tqdm
from spacy import load as spacy_load


logger = getLogger()
logger.setLevel(INFO)
fmt = Formatter('%(asctime)s: [ %(message)s ]', '%m/%d/%Y %I:%M:%S %p')
console = StreamHandler()
console.setFormatter(fmt)
logger.addHandler(console)

nlp = spacy_load("en_core_web_lg", disable=['parser'])

# ------------------------------------------------------------------------------
# Import helper
# ------------------------------------------------------------------------------

PREPROCESS_FN = None

def init(filename):
    global PREPROCESS_FN
    if filename:
        PREPROCESS_FN = import_module(filename).preprocess


def import_module(filename):
    """Import a module given a full path to the file."""
    spec = spec_from_file_location('doc_filter', filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------
# Store corpus.
# ------------------------------------------------------------------------------


def iter_files(path):
    """Walk through all files located under a root path."""
    if os_path_isfile(path):
        yield path
    elif os_path_isdir(path):
        for dirpath, _, filenames in os_walk(path):
            for f in filenames:
                yield os_path_join(dirpath, f)
    else:
        raise RuntimeError('Path %s is invalid' % path)


def get_contents(filename):
    """Parse the contents of a file. Each line is a JSON encoded document."""
    global PREPROCESS_FN
    documents = []
    with bz2_open(filename, 'rb') as f:
        for line in f:
            # Parse document
            doc = json_loads(line)
            # Maybe preprocess the document with custom function
            if PREPROCESS_FN:
                doc = PREPROCESS_FN(doc)
            # Skip if it is empty or None
            if not doc:
                continue
            # Add the document
            doc_text = doc.pop('text')
            doc_text_with_links = doc.pop('text_with_links')
            len_doc_text = len(doc_text)
            assert len_doc_text == len(doc_text_with_links)
            _text, _text_with_links = pickle_dumps(doc_text), pickle_dumps(doc_text_with_links)

            _text_ner = []
            for sent in doc_text:
                ent_list = [(ent.text, ent.start_char, ent.end_char, ent.label_) for ent in nlp(sent).ents]
                _text_ner.append(ent_list)
            _text_ner_str = pickle_dumps(_text_ner)

            documents.append((unicodedata_normalize('NFD', doc.pop('id')), doc.pop('url'), doc.pop('title'), _text, _text_with_links, _text_ner_str, len_doc_text))

    return documents


def store_contents(data_path, save_path, preprocess, num_workers=None):
    """Preprocess and store a corpus of documents in sqlite.

    Args:
        data_path: Root path to directory (or directory of directories) of files
          containing json encoded documents (must have `id` and `text` fields).
        save_path: Path to output sqlite db.
        preprocess: Path to file defining a custom `preprocess` function. Takes
          in and outputs a structured doc.
        num_workers: Number of parallel processes to use when reading docs.
    """
    if os_path_isfile(save_path):
        raise RuntimeError(f'{save_path} already exists! Not overwriting.')

    logger.info('Reading into database...')
    conn = sqlite3_connect(save_path)
    c = conn.cursor()
    c.execute("CREATE TABLE documents (id PRIMARY KEY, url, title, text, text_with_links, text_ner, sent_num);")

    files = list(iter_files(data_path))
    with Pool(num_workers, initializer=init, initargs=(preprocess,)) as workers, tqdm(total=len(files)) as pbar:
        count = 0
        for pairs in tqdm(workers.imap_unordered(get_contents, files)):
            count += len(pairs)
            c.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", pairs)
            pbar.update()
    logger.info('Read %d docs.' % count)
    logger.info('Committing...')
    conn.commit()
    conn.close()


# ------------------------------------------------------------------------------
# Main.
# ------------------------------------------------------------------------------


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('data_path', type=str, help='/path/to/data')
    parser.add_argument('save_path', type=str, help='/path/to/saved/db.db')
    parser.add_argument('--preprocess', type=str, default=None,
                        help=('File path to a python module that defines '
                              'a `preprocess` function'))
    parser.add_argument('--num-workers', type=int, default=None,
                        help='Number of CPU processes (for tokenizing, etc)')
    args = parser.parse_args()

    store_contents(
        args.data_path, args.save_path, args.preprocess, args.num_workers
    )
