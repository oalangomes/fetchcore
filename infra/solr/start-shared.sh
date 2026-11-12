#!/bin/sh
set -eu
export FETCHCORE_SOLR_CREATE_COLLECTION=false
exec sh /fetchcore-start-cloud.sh
