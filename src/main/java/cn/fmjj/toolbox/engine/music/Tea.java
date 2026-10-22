package cn.fmjj.toolbox.engine.music;

/**
 * TEA 解密（QQ 音乐 EncV2 密钥用）。
 * 移植自 Python music_unlock.tea_decrypt_block / tea_decrypt_cbc（TarsCpp TCTEA）。
 */
final class Tea {

    private static final int DELTA = 0x9E3779B9;

    private Tea() {}

    /** 标准 TEA 解密一个 8 字节块（16 字节 key）。 */
    static byte[] decryptBlock(byte[] block, byte[] key, int rounds) {
        long v0 = readIntBE(block, 0);
        long v1 = readIntBE(block, 4);
        long k0 = readIntBE(key, 0);
        long k1 = readIntBE(key, 4);
        long k2 = readIntBE(key, 8);
        long k3 = readIntBE(key, 12);
        long summ = (DELTA * (long) (rounds / 2)) & 0xFFFFFFFFL;
        for (int i = 0; i < rounds / 2; i++) {
            v1 = (v1 - (((v0 << 4) + k2) ^ (v0 + summ) ^ ((v0 >> 5) + k3))) & 0xFFFFFFFFL;
            v0 = (v0 - (((v1 << 4) + k0) ^ (v1 + summ) ^ ((v1 >> 5) + k1))) & 0xFFFFFFFFL;
            summ = (summ - DELTA) & 0xFFFFFFFFL;
        }
        byte[] out = new byte[8];
        writeIntBE(out, 0, v0);
        writeIntBE(out, 4, v1);
        return out;
    }

    /** TarsCpp TCTEA（CBC + PadLen/Salt/Zero 头），返回明文 body。 */
    static byte[] decryptCbc(byte[] data, byte[] key, int rounds) {
        if (data.length < 16 || data.length % 8 != 0) {
            throw new IllegalArgumentException("invalid tea cipher data length");
        }
        byte[] dest = decryptBlock(java.util.Arrays.copyOfRange(data, 0, 8), key, rounds);
        int padLen = dest[0] & 0x7;
        int outLen = data.length - 1 - padLen - 2 - 7;
        if (outLen < 0) throw new IllegalArgumentException("estimated plaindata length is less than 0");

        byte[] out = new byte[outLen];
        byte[] ivPrev = new byte[8];
        byte[] ivCur = java.util.Arrays.copyOfRange(data, 0, 8);
        int[] state = {8, 1 + padLen};  // dataPos, destIdx

        // 跳过 salt（2 字节）
        int i = 1;
        while (i <= 2) {
            if (state[1] < 8) {
                state[1]++;
                i++;
            } else {
                cryptBlock(data, key, rounds, dest, ivPrev, ivCur, state);
            }
        }

        int outPos = 0;
        while (outPos < outLen) {
            if (state[1] < 8) {
                out[outPos] = (byte) (dest[state[1]] ^ ivPrev[state[1]]);
                outPos++;
                state[1]++;
            } else {
                cryptBlock(data, key, rounds, dest, ivPrev, ivCur, state);
            }
        }
        return out;
    }

    private static void cryptBlock(byte[] data, byte[] key, int rounds, byte[] dest,
                                   byte[] ivPrev, byte[] ivCur, int[] state) {
        System.arraycopy(ivCur, 0, ivPrev, 0, 8);
        System.arraycopy(data, state[0], ivCur, 0, 8);
        byte[] xored = new byte[8];
        for (int j = 0; j < 8; j++) xored[j] = (byte) (dest[j] ^ ivCur[j]);
        byte[] dec = decryptBlock(xored, key, rounds);
        System.arraycopy(dec, 0, dest, 0, 8);
        state[0] += 8;
        state[1] = 0;
    }

    private static long readIntBE(byte[] b, int off) {
        return ((long) (b[off] & 0xFF) << 24) | ((b[off + 1] & 0xFF) << 16)
                | ((b[off + 2] & 0xFF) << 8) | (b[off + 3] & 0xFF);
    }

    private static void writeIntBE(byte[] b, int off, long v) {
        b[off] = (byte) ((v >> 24) & 0xFF);
        b[off + 1] = (byte) ((v >> 16) & 0xFF);
        b[off + 2] = (byte) ((v >> 8) & 0xFF);
        b[off + 3] = (byte) (v & 0xFF);
    }
}
