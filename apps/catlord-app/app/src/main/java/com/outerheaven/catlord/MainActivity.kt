package com.outerheaven.catlord

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.content.ContextCompat
import com.outerheaven.catlord.ui.MainScreen
import com.outerheaven.catlord.ui.MainViewModel
import com.outerheaven.catlord.ui.theme.CatlordTheme

class MainActivity : ComponentActivity() {

    private val viewModel: MainViewModel by viewModels()

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

        setContent {
            CatlordTheme {
                MainScreen(
                    viewModel = viewModel,
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
}
