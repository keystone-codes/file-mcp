"""Vendored copy of Keystone's identity verifier.

The module is copied, not depended on: keystone-mcp does not publish it as a
package. Refresh it only by replacing the file and the hash below together.
"""

KEYSTONE_IDENTITY_SOURCE = (
    "keystone-codes/keystone-mcp@6b04a198101dac22815b17d5bcf55b0e1c6463d8:"
    "examples/keystone-identity/python/keystone_identity.py"
)
KEYSTONE_IDENTITY_SHA256 = "50058263846b41b5c9a88e303b6e9ac6768297d8f7f6564f4a4c0da247b4429f"
