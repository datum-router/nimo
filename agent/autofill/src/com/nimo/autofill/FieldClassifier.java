package com.nimo.autofill;

/**
 * Pure field-classification logic for the nimo AutofillService.
 *
 * Kept free of android.* imports so it can be unit-tested on a plain JVM:
 * the service extracts (hints, emailLike, passwordLike, idEntry) from the
 * AssistStructure and delegates the decision here.
 */
public final class FieldClassifier {
    private FieldClassifier() {}

    public static final int NONE = 0;
    public static final int USERNAME = 1;
    public static final int PASSWORD = 2;

    /**
     * @param hints        autofill hints from the view, may be null
     * @param emailLike    inputType suggests email/username (caller's call)
     * @param passwordLike inputType suggests password (caller's call)
     * @param idEntry      view id entry name, may be null
     */
    public static int classify(String[] hints, boolean emailLike,
                               boolean passwordLike, String idEntry) {
        if (hints != null) {
            for (String h : hints) {
                if (h == null) continue;
                String lh = h.toLowerCase();
                if (lh.contains("password")) return PASSWORD;
                if (lh.contains("username") || lh.contains("email")) return USERNAME;
            }
        }
        if (passwordLike) return PASSWORD;
        if (emailLike) return USERNAME;
        String id = idEntry == null ? "" : idEntry.toLowerCase();
        if (id.contains("pass")) return PASSWORD;
        if (id.contains("user") || id.contains("email") || id.contains("login"))
            return USERNAME;
        return NONE;
    }
}
