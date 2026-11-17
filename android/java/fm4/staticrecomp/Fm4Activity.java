package fm4.staticrecomp;

import android.content.Intent;
import android.content.res.AssetManager;
import android.os.Build;
import android.os.Bundle;
import android.system.ErrnoException;
import android.system.Os;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import org.libsdl.app.SDLActivity;

/**
 * SDL's activity with FM4's folders, all in the app's external files folder (no permission
 * needed, reachable over adb and MTP):
 *
 *   files/game   the files extracted from the player's disc (the folder holding default.xex)
 *   files/state  saves, settings (config/graphics.toml), the shader cache and logs
 *
 * More game arguments come from state/config/arguments.txt (one per line) and, for tooling, a
 * string-array extra named args:
 *
 *   adb shell am start -n fm4.staticrecomp/.Fm4Activity --esa args --log_level=debug
 */
public class Fm4Activity extends SDLActivity {
    private static final String TAG = "FM4";
    // Mesa Turnip, packaged in assets/drivers (built by StevenMXZ from Mesa, MIT): on Adreno 7xx
    // it runs the game faster than the system driver (Pinyon Shift measured it for Forza Horizon).
    private static final String BUNDLED_DRIVER = "turnip-gen8-v37";

    private File base() {
        File external = getExternalFilesDir(null);
        return external != null ? external : getFilesDir();
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        File base = base();
        setenv("HOME", getFilesDir().getAbsolutePath());
        // Custom Vulkan drivers (Mesa Turnip) copied into state/drivers, which
        // android_gpu_driver picks from.
        setenv("REX_ANDROID_DRIVERS_DIR", new File(base, "state/drivers").getAbsolutePath());
        // Lossless Scaling frame generation reads its shaders from the player's own
        // Lossless.dll, put in state/lsfg (never packaged).
        setenv("REX_LSFG_DLL", new File(base, "state/lsfg/Lossless.dll").getAbsolutePath());
        for (String folder : new String[] {"game", "state/user", "state/cache", "state/config",
                                           "state/logs", "state/drivers", "state/lsfg"}) {
            new File(base, folder).mkdirs();
        }
        // android_gpu_driver's default, auto, loads the recommended driver: the bundled Turnip
        // on Qualcomm's chips, the system's elsewhere.
        // Every driver the package carries goes to state/drivers, where the ANDROID page's
        // GPU DRIVER row picks one.
        try {
            String[] drivers = getAssets().list("drivers");
            if (drivers != null) {
                for (String driver : drivers) {
                    installBundledDriver(driver, new File(base, "state/drivers/" + driver));
                }
            }
        } catch (IOException error) {
            Log.w(TAG, "bundled drivers not listed", error);
        }
        if (new File(base, "state/drivers/" + BUNDLED_DRIVER + "/meta.json").isFile()
                && "QTI".equalsIgnoreCase(Build.SOC_MANUFACTURER)) {
            setenv("REX_ANDROID_RECOMMENDED_DRIVER", BUNDLED_DRIVER);
        }
        super.onCreate(savedInstanceState);
    }

    // The C++ runtime, then the ShiftGlue runtime (it holds SDL and registers its Java natives
    // when loaded), then the game, libmain.so, whose SDL_main SDL calls.
    @Override
    protected String[] getLibraries() {
        return new String[] {"c++_shared", "rexruntime", "main"};
    }

    @Override
    protected String[] getArguments() {
        File base = base();
        List<String> arguments = new ArrayList<>();
        arguments.add("--game_data_root=" + new File(base, "game").getAbsolutePath());
        arguments.add("--user_data_root=" + new File(base, "state/user").getAbsolutePath());
        arguments.add("--cache_root=" + new File(base, "state/cache").getAbsolutePath());
        arguments.add("--fm4_config_root=" + new File(base, "state/config").getAbsolutePath());
        arguments.add("--log_file=" + new File(base, "state/logs/runtime.log").getAbsolutePath());
        // More arguments, one per line, from state/config/arguments.txt (kept across starts from
        // the app icon), then the intent's.
        File file = new File(base, "state/config/arguments.txt");
        if (file.isFile()) {
            try (BufferedReader reader = new BufferedReader(new FileReader(file))) {
                for (String line; (line = reader.readLine()) != null; ) {
                    if (line.trim().startsWith("--")) arguments.add(line.trim());
                }
            } catch (IOException error) {
                Log.w(TAG, "arguments.txt not read", error);
            }
        }
        Intent intent = getIntent();
        String[] given = intent != null ? intent.getStringArrayExtra("args") : null;
        if (given != null) Collections.addAll(arguments, given);
        return arguments.toArray(new String[0]);
    }

    // Copies the packaged driver's files where android_gpu_driver finds them, unless the same
    // sizes are already there; true when the driver is in place.
    private boolean installBundledDriver(String driver, File folder) {
        AssetManager assets = getAssets();
        String source = "drivers/" + driver;
        try {
            String[] names = assets.list(source);
            if (names == null || names.length == 0) return false;
            folder.mkdirs();
            byte[] buffer = new byte[1 << 16];
            for (String name : names) {
                File target = new File(folder, name);
                try (InputStream in = assets.open(source + "/" + name)) {
                    long size = assets.openFd(source + "/" + name).getLength();
                    if (target.isFile() && target.length() == size) continue;
                    try (OutputStream out = new FileOutputStream(target)) {
                        for (int read; (read = in.read(buffer)) > 0; ) out.write(buffer, 0, read);
                    }
                }
            }
            return true;
        } catch (IOException error) {
            Log.e(TAG, "bundled driver not installed", error);
            return new File(folder, "meta.json").isFile();
        }
    }

    private static void setenv(String name, String value) {
        try {
            Os.setenv(name, value, true);
        } catch (ErrnoException error) {
            Log.e(TAG, "setenv " + name + " failed", error);
        }
    }
}
