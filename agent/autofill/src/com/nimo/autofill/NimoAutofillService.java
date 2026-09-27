package com.nimo.autofill;

import android.app.assist.AssistStructure;
import android.os.CancellationSignal;
import android.service.autofill.Dataset;
import android.service.autofill.FillCallback;
import android.service.autofill.FillContext;
import android.service.autofill.FillRequest;
import android.service.autofill.FillResponse;
import android.service.autofill.SaveCallback;
import android.service.autofill.SaveRequest;
import android.text.InputType;
import android.view.autofill.AutofillId;
import android.view.autofill.AutofillValue;
import android.widget.RemoteViews;

import java.util.List;

/**
 * nimo's own AutofillService, installed on every test device.
 *
 * Credentials are pushed by the nimo agent to
 * /data/local/tmp/nimo-autofill.json (ephemeral CI device):
 *
 *   {"default": {"username": "u", "password": "p"},
 *    "com.example.app": {"username": "u2", "password": "p2"}}
 *
 * On a fill request the service walks the AssistStructure, finds the
 * username and password fields (hints -> inputType -> id heuristics, see
 * FieldClassifier), and offers a single "nimo test login" dataset. The
 * nimo crawler taps the username field, taps the offered dataset, and taps
 * the app's login button. No per-app scripting, no root, no typing
 * flakiness: values go through the OS autofill contract.
 *
 * The file is re-read on every request so credential rotation needs no
 * service restart. If the file is missing or unparseable, the request is
 * answered with no datasets (never a crash, never a fill of stale data).
 */
public class NimoAutofillService extends android.service.autofill.AutofillService {

    static final String DATASET_LABEL = "nimo test login";

    @Override
    public void onFillRequest(FillRequest request, CancellationSignal cancellationSignal,
                              FillCallback callback) {
        AssistStructure structure = latestStructure(request);
        if (structure == null) {
            callback.onSuccess(null);
            return;
        }
        String pkg = packageOf(structure);

        AutofillId usernameId = null;
        AutofillId passwordId = null;
        int windows = structure.getWindowNodeCount();
        for (int i = 0; i < windows && (usernameId == null || passwordId == null); i++) {
            AssistStructure.ViewNode root =
                    structure.getWindowNodeAt(i).getRootViewNode();
            AutofillId[] found = scan(root);
            if (usernameId == null) usernameId = found[0];
            if (passwordId == null) passwordId = found[1];
        }
        if (usernameId == null && passwordId == null) {
            callback.onSuccess(null); // no login-like fields on screen
            return;
        }

        String[] creds = CredStore.credentialsFor(pkg);
        if (creds == null) {
            callback.onSuccess(null); // no credentials provisioned
            return;
        }

        RemoteViews presentation =
                new RemoteViews(getPackageName(), android.R.layout.simple_list_item_1);
        presentation.setTextViewText(android.R.id.text1, DATASET_LABEL);
        Dataset.Builder db = new Dataset.Builder(presentation);
        if (usernameId != null) db.setValue(usernameId, AutofillValue.forText(creds[0]));
        if (passwordId != null) db.setValue(passwordId, AutofillValue.forText(creds[1]));
        FillResponse response = new FillResponse.Builder().addDataset(db.build()).build();
        callback.onSuccess(response);
    }

    @Override
    public void onSaveRequest(SaveRequest request, SaveCallback callback) {
        // nimo never saves credentials back; the vault is the source of truth.
        callback.onSuccess();
    }

    /** Returns {usernameId, passwordId} found under node (depth-first, first wins). */
    private static AutofillId[] scan(AssistStructure.ViewNode node) {
        AutofillId[] out = new AutofillId[]{null, null};
        scanInto(node, out);
        return out;
    }

    private static void scanInto(AssistStructure.ViewNode node, AutofillId[] out) {
        if (node == null || (out[0] != null && out[1] != null)) return;
        AutofillId id = node.getAutofillId();
        if (id != null
                && node.getAutofillType()
                   == android.view.View.AUTOFILL_TYPE_TEXT) {
            int kind = FieldClassifier.classify(
                    node.getAutofillHints(),
                    isEmailVariation(node.getInputType()),
                    isPasswordVariation(node.getInputType()),
                    node.getIdEntry());
            if (kind == FieldClassifier.USERNAME && out[0] == null) out[0] = id;
            else if (kind == FieldClassifier.PASSWORD && out[1] == null) out[1] = id;
        }
        int kids = node.getChildCount();
        for (int i = 0; i < kids && (out[0] == null || out[1] == null); i++) {
            scanInto(node.getChildAt(i), out);
        }
    }

    private static boolean isEmailVariation(int inputType) {
        int v = inputType & InputType.TYPE_MASK_VARIATION;
        return v == InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
                || v == InputType.TYPE_TEXT_VARIATION_WEB_EMAIL_ADDRESS;
    }

    private static boolean isPasswordVariation(int inputType) {
        int v = inputType & InputType.TYPE_MASK_VARIATION;
        return v == InputType.TYPE_TEXT_VARIATION_PASSWORD
                || v == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                || v == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD;
    }

    private static AssistStructure latestStructure(FillRequest request) {
        try {
            List<FillContext> contexts = request.getFillContexts();
            return contexts.get(contexts.size() - 1).getStructure();
        } catch (Exception e) {
            return null;
        }
    }

    private static String packageOf(AssistStructure structure) {
        try {
            return structure.getActivityComponent().getPackageName();
        } catch (Exception e) {
            return "";
        }
    }
}
