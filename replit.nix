{pkgs}: {
  deps = [
    pkgs.glib
    pkgs.dbus
    pkgs.alsa-lib
    pkgs.cairo
    pkgs.pango
    pkgs.mesa
    pkgs.libdrm
    pkgs.at-spi2-core
    pkgs.at-spi2-atk
    pkgs.atk
    pkgs.nspr
    pkgs.nss
    pkgs.chromium
  ];
}
