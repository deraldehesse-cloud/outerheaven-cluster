package com.newhorizon.voiceroadmap

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.content.ContextCompat
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInAccount
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.common.api.Scope
import com.google.api.services.drive.DriveScopes
import com.newhorizon.voiceroadmap.ui.MainScreen
import com.newhorizon.voiceroadmap.ui.MainViewModel
import com.newhorizon.voiceroadmap.ui.theme.NewHorizonVoiceRoadmapTheme

class MainActivity : ComponentActivity() {

    private val viewModel: MainViewModel by viewModels()

    // Google Sign-In Launcher
    private val googleSignInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val task = GoogleSignIn.getSignedInAccountFromIntent(result.data)
        try {
            val account: GoogleSignInAccount = task.getResult(ApiException::class.java)
            viewModel.setGoogleAccount(account)
            Toast.makeText(this, "Erfolgreich mit Google Drive verbunden", Toast.LENGTH_SHORT).show()
        } catch (e: ApiException) {
            e.printStackTrace()
            Toast.makeText(this, "Google Login fehlgeschlagen: ${e.statusCode}", Toast.LENGTH_LONG).show()
        }
    }

    // Audio Permission Launcher
    private val requestAudioPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            Toast.makeText(this, "Mikrofon freigegeben", Toast.LENGTH_SHORT).show()
        } else {
            Toast.makeText(this, "Mikrofonberechtigung für Spracheingabe erforderlich", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Automatisch prüfen, ob bereits ein Google-Konto hinterlegt ist
        val existingAccount = GoogleSignIn.getLastSignedInAccount(this)
        if (existingAccount != null) {
            viewModel.setGoogleAccount(existingAccount)
        }

        setContent {
            NewHorizonVoiceRoadmapTheme {
                MainScreen(
                    viewModel = viewModel,
                    onSignInClick = { launchGoogleSignIn() },
                    onRequestMicrophonePermission = { checkAndRequestAudioPermission() }
                )
            }
        }
    }

    private fun checkAndRequestAudioPermission() {
        if (ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.RECORD_AUDIO
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            requestAudioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun launchGoogleSignIn() {
        val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestEmail()
            .requestScopes(Scope(DriveScopes.DRIVE_FILE))
            .build()

        val googleSignInClient = GoogleSignIn.getClient(this, gso)
        googleSignInLauncher.launch(googleSignInClient.signInIntent)
    }
}
