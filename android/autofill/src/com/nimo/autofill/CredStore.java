package com.nimo.autofill;

import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.Reader;

/**
 * Reads the credential file pushed by the nimo agent. Zero dependencies,
 * no android.* imports: unit-testable on a plain JVM.
 *
 * Shape:
 *   {"default": {"username": "u", "password": "p"},
 *    "com.example.app": {"username": "u2", "password": "p2"}}
 */
public final class CredStore {
    private CredStore() {}

    public static final String CRED_PATH = "/data/local/tmp/nimo-autofill.json";

    /** Returns {username, password} for pkg, falling back to "default", or null. */
    public static String[] credentialsFor(String pkg) {
        String json = readFile(CRED_PATH);
        if (json == null) return null;
        String[] per = extractPair(json, pkg);
        if (per != null) return per;
        return extractPair(json, "default");
    }

    /** Same lookup against an in-memory document (for tests). */
    public static String[] credentialsFor(String json, String pkg) {
        if (json == null) return null;
        String[] per = extractPair(json, pkg);
        if (per != null) return per;
        return extractPair(json, "default");
    }

    private static String readFile(String path) {
        StringBuilder sb = new StringBuilder();
        try {
            Reader r = new InputStreamReader(new FileInputStream(path), "UTF-8");
            char[] buf = new char[4096];
            int n;
            while ((n = r.read(buf)) > 0) sb.append(buf, 0, n);
            r.close();
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    /** Find "key": {"username": "u", "password": "p"} and return {u, p}. */
    static String[] extractPair(String json, String key) {
        int ki = json.indexOf('"' + key + '"');
        if (ki < 0) return null;
        int brace = json.indexOf('{', ki);
        if (brace < 0) return null;
        int end = matchBrace(json, brace);
        if (end < 0) return null;
        String obj = json.substring(brace, end + 1);
        String u = extractString(obj, "username");
        String p = extractString(obj, "password");
        if (u == null || p == null) return null;
        return new String[]{u, p};
    }

    private static int matchBrace(String s, int open) {
        int depth = 0;
        boolean inStr = false;
        for (int i = open; i < s.length(); i++) {
            char c = s.charAt(i);
            if (inStr) {
                if (c == '\\') i++;
                else if (c == '"') inStr = false;
            } else if (c == '"') {
                inStr = true;
            } else if (c == '{') {
                depth++;
            } else if (c == '}') {
                if (--depth == 0) return i;
            }
        }
        return -1;
    }

    private static String extractString(String obj, String field) {
        int fi = obj.indexOf('"' + field + '"');
        if (fi < 0) return null;
        int colon = obj.indexOf(':', fi);
        if (colon < 0) return null;
        int q1 = obj.indexOf('"', colon);
        if (q1 < 0) return null;
        StringBuilder sb = new StringBuilder();
        for (int i = q1 + 1; i < obj.length(); i++) {
            char c = obj.charAt(i);
            if (c == '\\' && i + 1 < obj.length()) {
                char e = obj.charAt(++i);
                sb.append(e == 'n' ? '\n' : e == 't' ? '\t' : e);
            } else if (c == '"') {
                return sb.toString();
            } else {
                sb.append(c);
            }
        }
        return null;
    }
}
