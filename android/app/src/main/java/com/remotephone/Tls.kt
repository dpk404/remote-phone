package com.remotephone

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.security.auth.x500.X500Principal

/**
 * The phone's TLS identity: a keystore-held key with a self-signed certificate, made once and
 * kept until the app's data is cleared. Desktops pin its fingerprint the first time they connect.
 */
object Tls {
    private const val ALIAS = "remotephone-tls"

    private fun keyStore(): KeyStore {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!ks.containsAlias(ALIAS)) {
            KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply {
                initialize(
                    KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                        // TLS hashes the handshake itself and hands the keystore a raw digest to sign,
                        // so DIGEST_NONE must be allowed or every handshake fails with "Incompatible digest"
                        .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256,
                            KeyProperties.DIGEST_SHA384, KeyProperties.DIGEST_SHA512)
                        .setCertificateSubject(X500Principal("CN=RemotePhone"))
                        .setCertificateNotAfter(Date(System.currentTimeMillis() + 50L * 365 * 24 * 3600 * 1000))
                        .build()
                )
            }.generateKeyPair()
        }
        return ks
    }

    fun serverContext(): SSLContext {
        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(keyStore(), null) }
        return SSLContext.getInstance("TLS").apply { init(kmf.keyManagers, null, null) }
    }

    /** SHA-256 of the certificate as uppercase hex; clients bind their authentication to it. */
    fun fingerprintHex(): String =
        MessageDigest.getInstance("SHA-256").digest(keyStore().getCertificate(ALIAS).encoded)
            .joinToString("") { "%02X".format(it) }
}
