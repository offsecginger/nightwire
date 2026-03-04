package org.whispersystems.signalservice.api.registration;

import org.signal.core.models.AccountEntropyPool;
import org.signal.core.models.MasterKey;
import org.signal.core.models.ServiceId;
import org.signal.core.models.backup.MediaRootBackupKey;
import org.signal.libsignal.protocol.IdentityKey;
import org.signal.libsignal.protocol.IdentityKeyPair;
import org.signal.libsignal.protocol.InvalidKeyException;
import org.signal.libsignal.protocol.ecc.ECPrivateKey;
import org.signal.libsignal.protocol.ecc.ECPublicKey;
import org.signal.libsignal.protocol.util.ByteUtil;
import org.signal.libsignal.zkgroup.InvalidInputException;
import org.signal.libsignal.zkgroup.profiles.ProfileKey;
import org.whispersystems.signalservice.api.account.AccountAttributes;
import org.whispersystems.signalservice.api.account.PreKeyCollection;
import org.whispersystems.signalservice.api.util.CredentialsProvider;
import org.whispersystems.signalservice.internal.push.ProvisionMessage;
import org.whispersystems.signalservice.internal.push.ProvisioningSocket;
import org.whispersystems.signalservice.internal.push.PushServiceSocket;
import org.whispersystems.signalservice.internal.util.DynamicCredentialsProvider;

import java.io.IOException;
import java.util.concurrent.TimeoutException;

public class ProvisioningApi {
    private final PushServiceSocket pushServiceSocket;
    private final ProvisioningSocket provisioningSocket;
    private final CredentialsProvider credentials;

    public ProvisioningApi(PushServiceSocket pushServiceSocket, ProvisioningSocket provisioningSocket, CredentialsProvider credentials) {
        this.pushServiceSocket = pushServiceSocket;
        this.provisioningSocket = provisioningSocket;
        this.credentials = credentials;
    }

    public String getNewDeviceUuid() throws TimeoutException, IOException {
        return provisioningSocket.getProvisioningUuid().address;
    }

    public NewDeviceRegistrationReturn getNewDeviceRegistration(IdentityKeyPair tempIdentityKey) throws TimeoutException, IOException {
        ProvisionMessage msg = provisioningSocket.getProvisioningMessage(tempIdentityKey);
        String number = msg.number;

        // PATCHED: use two-argument parseOrThrow to handle binary ACI/PNI
        ServiceId.ACI aci = ServiceId.ACI.parseOrThrow(msg.aci, msg.aciBinary);
        ServiceId.PNI pni = ServiceId.PNI.parseOrThrow(msg.pni, msg.pniBinary);

        if (credentials instanceof DynamicCredentialsProvider) {
            ((DynamicCredentialsProvider) credentials).setE164(number);
        }

        IdentityKeyPair aciIdentityKeyPair = getIdentityKeyPair(
                msg.aciIdentityKeyPublic.toByteArray(),
                msg.aciIdentityKeyPrivate.toByteArray());

        IdentityKeyPair pniIdentityKeyPair;
        if (msg.pniIdentityKeyPublic != null && msg.pniIdentityKeyPrivate != null) {
            pniIdentityKeyPair = getIdentityKeyPair(
                    msg.pniIdentityKeyPublic.toByteArray(),
                    msg.pniIdentityKeyPrivate.toByteArray());
        } else {
            pniIdentityKeyPair = null;
        }

        ProfileKey profileKey;
        try {
            if (msg.profileKey != null) {
                profileKey = new ProfileKey(msg.profileKey.toByteArray());
            } else {
                profileKey = null;
            }
        } catch (InvalidInputException e) {
            throw new IOException("Failed to decrypt profile key", e);
        }

        MasterKey masterKey;
        try {
            if (msg.masterKey != null) {
                masterKey = new MasterKey(msg.masterKey.toByteArray());
            } else {
                masterKey = null;
            }
        } catch (AssertionError e) {
            throw new IOException("Failed to decrypt master key", e);
        }

        AccountEntropyPool accountEntropyPool;
        if (msg.accountEntropyPool != null) {
            accountEntropyPool = new AccountEntropyPool(msg.accountEntropyPool);
        } else {
            accountEntropyPool = null;
        }

        MediaRootBackupKey mediaRootBackupKey;
        if (msg.mediaRootBackupKey != null && msg.mediaRootBackupKey.size() == 32) {
            mediaRootBackupKey = new MediaRootBackupKey(msg.mediaRootBackupKey.toByteArray());
        } else {
            mediaRootBackupKey = null;
        }

        String provisioningCode = msg.provisioningCode;
        boolean readReceipts = msg.readReceipts != null && msg.readReceipts;

        return new NewDeviceRegistrationReturn(
                provisioningCode, aciIdentityKeyPair, pniIdentityKeyPair,
                number, aci, pni, profileKey, masterKey,
                accountEntropyPool, mediaRootBackupKey, readReceipts);
    }

    private IdentityKeyPair getIdentityKeyPair(byte[] publicKeyBytes, byte[] privateKeyBytes) throws IOException {
        if (publicKeyBytes.length == 32) {
            byte[] prefix = new byte[]{5};
            publicKeyBytes = ByteUtil.combine(new byte[][]{prefix, publicKeyBytes});
        }
        try {
            ECPublicKey publicKey = new ECPublicKey(publicKeyBytes);
            ECPrivateKey privateKey = new ECPrivateKey(privateKeyBytes);
            return new IdentityKeyPair(new IdentityKey(publicKey), privateKey);
        } catch (InvalidKeyException e) {
            throw new IOException("Failed to decrypt key", e);
        }
    }

    public int finishNewDeviceRegistration(String code, AccountAttributes attrs, PreKeyCollection aciPreKeys, PreKeyCollection pniPreKeys) throws IOException {
        int deviceId = pushServiceSocket.finishNewDeviceRegistration(code, attrs, aciPreKeys, pniPreKeys);
        if (credentials instanceof DynamicCredentialsProvider) {
            ((DynamicCredentialsProvider) credentials).setDeviceId(deviceId);
        }
        return deviceId;
    }

    public static class NewDeviceRegistrationReturn {
        private final String provisioningCode;
        private final IdentityKeyPair aciIdentity;
        private final IdentityKeyPair pniIdentity;
        private final String number;
        private final ServiceId.ACI aci;
        private final ServiceId.PNI pni;
        private final ProfileKey profileKey;
        private final MasterKey masterKey;
        private final AccountEntropyPool accountEntropyPool;
        private final MediaRootBackupKey mediaRootBackupKey;
        private final boolean readReceipts;

        NewDeviceRegistrationReturn(String provisioningCode, IdentityKeyPair aciIdentityKeyPair,
                IdentityKeyPair pniIdentityKeyPair, String number, ServiceId.ACI aci, ServiceId.PNI pni,
                ProfileKey profileKey, MasterKey masterKey, AccountEntropyPool accountEntropyPool,
                MediaRootBackupKey mediaRootBackupKey, boolean readReceipts) {
            this.provisioningCode = provisioningCode;
            this.aciIdentity = aciIdentityKeyPair;
            this.pniIdentity = pniIdentityKeyPair;
            this.number = number;
            this.aci = aci;
            this.pni = pni;
            this.profileKey = profileKey;
            this.masterKey = masterKey;
            this.accountEntropyPool = accountEntropyPool;
            this.mediaRootBackupKey = mediaRootBackupKey;
            this.readReceipts = readReceipts;
        }

        public String getProvisioningCode() { return provisioningCode; }
        public IdentityKeyPair getAciIdentity() { return aciIdentity; }
        public IdentityKeyPair getPniIdentity() { return pniIdentity; }
        public String getNumber() { return number; }
        public ServiceId.ACI getAci() { return aci; }
        public ServiceId.PNI getPni() { return pni; }
        public ProfileKey getProfileKey() { return profileKey; }
        public MasterKey getMasterKey() { return masterKey; }
        public AccountEntropyPool getAccountEntropyPool() { return accountEntropyPool; }
        public MediaRootBackupKey getMediaRootBackupKey() { return mediaRootBackupKey; }
        public boolean isReadReceipts() { return readReceipts; }
    }
}
