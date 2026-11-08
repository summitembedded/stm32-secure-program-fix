#!/bin/bash
# Makes the three dev-only P-256 keypairs for the DA certificate chain: root, intermediate, leaf.
set -e
cd "$(dirname "$0")"
mkdir -p keys

# A board provisioned with the old root public key can only be unlocked by the old root keypair
for level in root intermediate leaf; do
    if [ -e "keys/${level}_private_key.pem" ]; then
        echo "keys/${level}_private_key.pem already exists, not overwriting it."
        exit 1
    fi
done

for level in root intermediate leaf; do
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "keys/${level}_private_key.pem"
    openssl pkey -in "keys/${level}_private_key.pem" -pubout -out "keys/${level}_public_key.pem"
    echo "keys/${level}_private_key.pem + keys/${level}_public_key.pem"
done
