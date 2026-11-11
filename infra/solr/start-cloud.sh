#!/bin/sh
set -eu

solr_url=http://127.0.0.1:8983/solr
configset_name=${FETCHCORE_SOLR_CONFIGSET:-fetchcore-projection-v3}
collection=${FETCHCORE_SOLR_COLLECTION:-fetchcore}
create_collection=${FETCHCORE_SOLR_CREATE_COLLECTION:-true}
zk_host=${ZK_HOST:-127.0.0.1:9983}
configset_dir=/fetchcore-configset
config_dir=$configset_dir/conf
temporary_dir=$(mktemp -d)
solr_pid=

case "$configset_name" in
    ""|*[!A-Za-z0-9_.-]*)
        echo "invalid Solr configset name" >&2
        exit 2
        ;;
esac
case "$collection" in
    ""|*[!A-Za-z0-9_.-]*)
        echo "invalid Solr collection name" >&2
        exit 2
        ;;
esac
case "$create_collection" in
    true|false) ;;
    *)
        echo "FETCHCORE_SOLR_CREATE_COLLECTION must be true or false" >&2
        exit 2
        ;;
esac

cleanup() {
    if [ -n "$solr_pid" ]; then
        kill "$solr_pid" 2>/dev/null || true
        wait "$solr_pid" 2>/dev/null || true
    fi
    rm -rf "$temporary_dir"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

solr-foreground -c &
solr_pid=$!

attempt=0
while [ "$attempt" -lt 120 ]; do
    if ! kill -0 "$solr_pid" 2>/dev/null; then
        echo "SolrCloud process exited before readiness" >&2
        exit 1
    fi
    cluster_status=$(curl -fsS \
        "$solr_url/admin/collections?action=CLUSTERSTATUS&wt=json" 2>/dev/null || true)
    if printf '%s' "$cluster_status" \
        | grep -Eq '"live_nodes"[[:space:]]*:[[:space:]]*\[[[:space:]]*"'; then
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ "$attempt" -ge 120 ]; then
    echo "SolrCloud did not report a live ZooKeeper cluster" >&2
    exit 1
fi

configsets=$(curl -fsS \
    "$solr_url/admin/configs?action=LIST&omitHeader=true&wt=json")
if ! printf '%s' "$configsets" | grep -F "\"$configset_name\"" >/dev/null; then
    solr zk upconfig -z "$zk_host" -n "$configset_name" -d "$config_dir"
    configsets=$(curl -fsS \
        "$solr_url/admin/configs?action=LIST&omitHeader=true&wt=json")
fi
if ! printf '%s' "$configsets" | grep -F "\"$configset_name\"" >/dev/null; then
    echo "SolrCloud configset upload was not visible" >&2
    exit 1
fi

downloaded_configset="$temporary_dir/configset"
solr zk downconfig -z "$zk_host" -n "$configset_name" -d "$downloaded_configset"
if ! diff -ru "$config_dir" "$downloaded_configset/conf"; then
    echo "SolrCloud configset $configset_name differs from the pinned local config" >&2
    exit 1
fi

if [ "$create_collection" = "true" ]; then
    collections=$(curl -fsS \
        "$solr_url/admin/collections?action=LIST&wt=json")
    if ! printf '%s' "$collections" | grep -F "\"$collection\"" >/dev/null; then
        curl -fsS "$solr_url/admin/collections?action=CREATE&name=$collection&collection.configName=$configset_name&numShards=1&replicationFactor=1&wt=json" \
            >/dev/null
    fi
    collection_status=$(curl -fsS \
        "$solr_url/admin/collections?action=CLUSTERSTATUS&collection=$collection&wt=json")
    if ! printf '%s' "$collection_status" \
        | grep -Eq '"configName"[[:space:]]*:[[:space:]]*"'$configset_name'"'; then
        echo "SolrCloud collection $collection does not use configset $configset_name" >&2
        exit 1
    fi

    curl -fsS "$solr_url/$collection/select?q=*:*&rows=0&wt=json" >/dev/null
fi

wait "$solr_pid"
