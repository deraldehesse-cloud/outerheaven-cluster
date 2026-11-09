package com.example.paperwork_scanner.navigation

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import com.example.paperwork_scanner.data.AuthPreferencesRepository
import com.example.paperwork_scanner.ui.screens.MainNavigationScreen
import com.example.paperwork_scanner.ui.screens.NavigationTab
import com.example.paperwork_scanner.ui.screens.SplashScreen

@Composable
fun AppNavHost(
    navController: NavHostController,
    authRepository: AuthPreferencesRepository,
    modifier: Modifier = Modifier
) {
    NavHost(
        navController = navController,
        startDestination = Screen.Splash.route,
        modifier = modifier
    ) {
        composable(Screen.Splash.route) {
            SplashScreen(
                onSplashFinished = {
                    navController.navigate(Screen.Main.route) {
                        popUpTo(Screen.Splash.route) {
                            inclusive = true
                        }
                    }
                }
            )
        }

        composable(Screen.Main.route) {
            MainNavigationScreen(
                authRepository = authRepository,
                initialTab = NavigationTab.SCANNER
            )
        }

        composable(Screen.Scanner.route) {
            MainNavigationScreen(
                authRepository = authRepository,
                initialTab = NavigationTab.SCANNER
            )
        }

        composable(Screen.EmailReview.route) {
            MainNavigationScreen(
                authRepository = authRepository,
                initialTab = NavigationTab.PHOBIE_SCHILD
            )
        }
    }
}
