"""Uji penanda CAPTCHA IDX dan sinyal berhentinya.

Dua hal berat diuji di sini. Pertama, kalimat persis yang disebutkan
operator -- ``<input type="checkbox" aria-label="Verify you are human">``
-- karena ejaan itulah yang dulu lolos: penanda sebelumnya berbunyi
"verifying you are human", dan keduanya tidak saling mengandung. Kedua,
bahwa :class:`CaptchaRequired` lolos dari ``except Exception``, yang menelan
pengecualian biasa di banyak lapis program ini -- tanpa sifat itu run akan
tetap berjalan melawan kotak yang tidak bisa dikliknya.
"""

from __future__ import annotations

import pytest
from firefox_bridge.captcha import (
    CAPTCHA_REASON,
    CaptchaRequired,
    is_captcha_page,
    popup_text,
)

CHECKBOX = '<input type="checkbox" aria-label="Verify you are human">'


def test_penanda_menangkap_markup_persis_dari_operator() -> None:
    assert is_captcha_page(CHECKBOX)


def test_penanda_tahan_terhadap_casing_dan_kutipan() -> None:
    assert is_captcha_page(CHECKBOX.upper())
    assert is_captcha_page("aria-label='verify you are human'")
    assert is_captcha_page("Silakan Verify You Are Human dahulu")


def test_wadah_cloudflare_saja_bukan_captcha() -> None:
    """Hanya frasanya, bukan wadahnya.

    Skrip Turnstile dan kontainer ``cf-turnstile`` ikut dimuat banyak halaman
    IDX yang sehat; menjadikannya penanda berarti popup muncul tanpa ada
    kotak yang perlu dicentang.
    """
    assert not is_captcha_page(
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
    )
    assert not is_captcha_page('<div class="cf-turnstile" data-sitekey="x"></div>')
    assert not is_captcha_page("PT Bursa Efek Indonesia " + "x" * 200)


def test_ejaan_lama_bukan_penanda_captha() -> None:
    """"Verifying you are human" adalah tantangan otomatis, bukan kotak.

    Tantangan itu justru yang selama ini bisa ditunggu sampai selesai sendiri.
    Kalau keduanya saling mengandung, menghapus salah satu bisa diam-diam
    mematikan yang lain -- jadi keduanya dikunci di sini.
    """
    assert not is_captcha_page("Verifying you are human")
    assert not is_captcha_page("Just a moment... checking your browser")


def test_captha_required_tidak_tertelan_except_exception() -> None:
    """Inilah alasan kelasnya turunan ``BaseException``.

    Program ini sengaja menangkap ``Exception`` di mana-mana agar satu
    saham yang gagal tidak mematikan seluruh run. Penanda berhenti harus
    melewati semua penangan itu, atau pesan "selesaikan CAPTCHA" tak akan
    pernah sampai dan run tetap mengklik kotak yang tidak terjawab.
    """
    swallowed = False

    with pytest.raises(CaptchaRequired):
        try:
            raise CaptchaRequired("berhenti")
        except Exception:  # noqa: BLE001 - penangan yang justru tak boleh menangkap
            swallowed = True

    assert not swallowed


def test_pesan_popup_memberi_alamat_tab_dua_pilihan() -> None:
    url = "https://www.idx.co.id/Portals/0/x/Audit/ARKA/instance.zip"
    teks = popup_text(url)

    assert url in teks
    assert "Verify you are human" in teks
    assert "DIHENTIKAN" in teks
    assert "OK" in teks and "Batal" in teks
    assert "download_history.json" in teks, "resume harus disebut, bukan disangka"


def test_pesan_popup_tanpa_alamat_memakai_homepage() -> None:
    assert "idx.co.id" in popup_text(None)


def test_alasan_klasifikasi_bukan_200() -> None:
    """Alasan CAPTCHA harus berbeda ejaan dari "file ada" -- dua cerita berbeda."""
    assert CAPTCHA_REASON != "200 (file exists; downloads.download failed)"
    assert "CAPTCHA" in CAPTCHA_REASON
