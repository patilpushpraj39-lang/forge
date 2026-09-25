# Sandbox Images

Every sandbox image is selected by immutable repository digest, runs as a
numeric non-root user, receives no control-plane secret, and is paired with a
documented language toolchain and resource policy.

The first CI abuse lane pulls the maintained Python 3.12 slim Bookworm tag,
resolves the local image to its repository digest, and passes only that immutable
reference to the controller. Mutable tags are rejected by the controller API.
