#!/bin/sh
# Install only the pinned configset; never create cores or rewrite existing config.
set -eu
target=/var/solr/data/configsets/fetchcore
if [ -e "$target" ]; then
    diff -r /fetchcore-configset "$target" || {
        echo 'Existing FetchCore configset differs; explicit operator migration required' >&2
        exit 1
    }
else
    mkdir -p /var/solr/data/configsets
    cp -R /fetchcore-configset "$target"
fi
exec solr-foreground
