import com.nimo.autofill.CredStore;
import com.nimo.autofill.FieldClassifier;

/** Plain-JVM tests for the android-free logic. Run: javac + java TestNimo. */
public class TestNimo {
    static int failures = 0;

    static void check(String name, Object got, Object want) {
        boolean ok = want == null ? got == null : want.equals(got);
        if (!ok) {
            failures++;
            System.out.println("FAIL " + name + ": got=" + got + " want=" + want);
        } else {
            System.out.println("ok   " + name);
        }
    }

    static void checkArr(String name, String[] got, String[] want) {
        String g = got == null ? null : String.join("|", got);
        String w = want == null ? null : String.join("|", want);
        check(name, g, w);
    }

    public static void main(String[] a) {
        int U = FieldClassifier.USERNAME, P = FieldClassifier.PASSWORD,
            N = FieldClassifier.NONE;

        // hints win
        check("hint password", FieldClassifier.classify(new String[]{"password"}, false, false, "username"), P);
        check("hint emailAddress", FieldClassifier.classify(new String[]{"emailAddress"}, false, false, null), U);
        check("hint username", FieldClassifier.classify(new String[]{"username"}, false, false, null), U);
        // inputType fallback
        check("email inputType", FieldClassifier.classify(null, true, false, null), U);
        check("password inputType", FieldClassifier.classify(null, false, true, null), P);
        // id heuristics fallback
        check("id etPassword", FieldClassifier.classify(null, false, false, "etPassword"), P);
        check("id username_input", FieldClassifier.classify(null, false, false, "username_input"), U);
        check("id loginEmail", FieldClassifier.classify(null, false, false, "loginEmail"), U);
        check("id submit -> none", FieldClassifier.classify(null, false, false, "submit"), N);
        check("all null -> none", FieldClassifier.classify(null, false, false, null), N);
        // hint beats misleading id
        check("hint password beats id user", FieldClassifier.classify(new String[]{"password"}, false, false, "user"), P);

        // CredStore
        String json = "{\"default\": {\"username\": \"d@x.com\", \"password\": \"s3cret\"},"
                + " \"com.app\": {\"username\": \"app@x.com\", \"password\": \"p@ss\\\"word\"}}";
        checkArr("per-package", CredStore.credentialsFor(json, "com.app"),
                new String[]{"app@x.com", "p@ss\"word"});
        checkArr("default fallback", CredStore.credentialsFor(json, "com.other"),
                new String[]{"d@x.com", "s3cret"});
        checkArr("null json", CredStore.credentialsFor(null, "com.app"), null);
        checkArr("empty", CredStore.credentialsFor("{}", "com.app"), null);
        checkArr("missing password", CredStore.credentialsFor(
                "{\"default\": {\"username\": \"u\"}}", "x"), null);
        checkArr("whitespace", CredStore.credentialsFor(
                "{ \"default\" : { \"username\" : \"u\" , \"password\" : \"p\" } }", "x"),
                new String[]{"u", "p"});

        if (failures > 0) {
            System.out.println(failures + " FAILURES");
            System.exit(1);
        }
        System.out.println("ALL TESTS PASSED");
    }
}
