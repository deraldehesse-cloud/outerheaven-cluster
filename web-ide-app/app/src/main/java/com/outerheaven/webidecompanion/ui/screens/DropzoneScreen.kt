package com.outerheaven.webidecompanion.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Android
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.webidecompanion.data.model.DropzoneFile
import com.outerheaven.webidecompanion.ui.theme.*
import com.outerheaven.webidecompanion.ui.viewmodel.MainViewModel
import com.outerheaven.webidecompanion.ui.viewmodel.UiState

@Composable
fun DropzoneScreen(
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    val dropzoneState by viewModel.dropzoneState.collectAsState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBackground)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "CATLORD DROPZONE",
                    style = MaterialTheme.typography.titleLarge,
                    color = AccentCyan,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp
                )
                Text(
                    text = "Eingangsordner für Reverse-Engineering",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
            IconButton(
                onClick = { viewModel.loadDropzone() },
                modifier = Modifier
                    .clip(CircleShape)
                    .background(DarkSurfaceVariant)
            ) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Dropzone aktualisieren",
                    tint = AccentCyan
                )
            }
        }

        when (val state = dropzoneState) {
            is UiState.Success -> {
                val files = state.data.files
                if (files.isEmpty()) {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Folder,
                                contentDescription = null,
                                modifier = Modifier.size(56.dp),
                                tint = TextMuted
                            )
                            Text(
                                text = "Keine APKs in der Dropzone",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextSecondary
                            )
                            Text(
                                text = "Lege .apk Dateien in /workspace/catlord-dropzone/ ab.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextMuted
                            )
                        }
                    }
                } else {
                    Text(
                        text = "${state.data.apk_count} APK-Pakete bereit zur Analyse:",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary
                    )

                    LazyColumn(
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                        modifier = Modifier.fillMaxSize()
                    ) {
                        items(files) { file ->
                            DropzoneFileCard(file = file)
                        }
                    }
                }
            }
            is UiState.Loading -> {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = AccentCyan)
                }
            }
            is UiState.Error -> {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "Fehler beim Laden der Dropzone",
                            style = MaterialTheme.typography.titleMedium,
                            color = AccentRed
                        )
                        Text(
                            text = state.message,
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextSecondary
                        )
                        Button(
                            onClick = { viewModel.loadDropzone() },
                            colors = ButtonDefaults.buttonColors(containerColor = DarkSurfaceVariant)
                        ) {
                            Text("Erneut versuchen")
                        }
                    }
                }
            }
            else -> {}
        }
    }
}

@Composable
fun DropzoneFileCard(file: DropzoneFile) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, DarkBorder, RoundedCornerShape(10.dp)),
        colors = CardDefaults.cardColors(containerColor = DarkSurface)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(AccentCyanDim.copy(alpha = 0.2f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.Default.Android,
                    contentDescription = null,
                    tint = AccentCyan,
                    modifier = Modifier.size(26.dp)
                )
            }

            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Text(
                    text = file.name,
                    style = MaterialTheme.typography.titleMedium,
                    color = TextPrimary,
                    fontWeight = FontWeight.Medium
                )
                Text(
                    text = file.modified_at.take(19).replace("T", " "),
                    style = MaterialTheme.typography.labelSmall,
                    color = TextMuted
                )
            }

            Surface(
                color = DarkSurfaceVariant,
                shape = RoundedCornerShape(6.dp)
            ) {
                Text(
                    text = file.size_formatted,
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = AccentCyan,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    fontWeight = FontWeight.Bold
                )
            }
        }
    }
}
